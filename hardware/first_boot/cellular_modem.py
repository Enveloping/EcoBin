from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import stat
import time
from typing import Protocol, Sequence

from .cellular_probe import UsbNetworkDevice


_TTY_NAME = re.compile(r"^ttyACM[0-9]{1,3}$")
_USB_ID = re.compile(r"^[0-9a-f]{4}$")
_REGISTRATION = re.compile(
    r"\+CEREG:\s*([0-5])(?:\s*,\s*([0-5]))?",
    re.IGNORECASE,
)
_PACKET_ATTACHED = re.compile(r"\+CGATT:\s*([01])", re.IGNORECASE)
_AT_COMMANDS = ("AT", "AT+CPIN?", "AT+CEREG?", "AT+CGATT?")


@dataclass(frozen=True, slots=True)
class AtControlPort:
    path: Path
    usb_vid: str
    usb_pid: str
    usb_parent_path: str


@dataclass(frozen=True, slots=True)
class ModemRegistration:
    ready: bool
    error_code: str


class AtControlPortInventory(Protocol):
    def ports(self) -> Sequence[AtControlPort]: ...


class AtCommandClient(Protocol):
    def query(self, port: Path, commands: tuple[str, ...]) -> dict[str, str]: ...


class AtQueryError(RuntimeError):
    pass


class SysfsAtControlPortInventory:
    """Find the read-only AT port belonging to the selected USB modem."""

    def __init__(
        self,
        sys_class_tty: Path = Path("/sys/class/tty"),
        device_root: Path = Path("/dev"),
    ) -> None:
        self._sys_class_tty = sys_class_tty
        self._device_root = device_root

    def ports(self) -> list[AtControlPort]:
        result: list[AtControlPort] = []
        try:
            entries = sorted(self._sys_class_tty.iterdir(), key=lambda item: item.name)
        except OSError:
            return result
        for entry in entries:
            if not _TTY_NAME.fullmatch(entry.name):
                continue
            try:
                interface = (entry / "device/interface").read_text(
                    encoding="ascii"
                ).strip().lower()
                device = (entry / "device").resolve(strict=True)
            except (OSError, UnicodeDecodeError):
                continue
            if interface != "at":
                continue
            parent = device
            for _ in range(12):
                vid_path = parent / "idVendor"
                pid_path = parent / "idProduct"
                if vid_path.is_file() and pid_path.is_file():
                    try:
                        vid = vid_path.read_text(encoding="ascii").strip().lower()
                        pid = pid_path.read_text(encoding="ascii").strip().lower()
                    except (OSError, UnicodeDecodeError):
                        break
                    if _USB_ID.fullmatch(vid) and _USB_ID.fullmatch(pid):
                        result.append(
                            AtControlPort(
                                path=self._device_root / entry.name,
                                usb_vid=vid,
                                usb_pid=pid,
                                usb_parent_path=str(parent.resolve()),
                            )
                        )
                    break
                if parent.parent == parent:
                    break
                parent = parent.parent
        return result


class SerialAtCommandClient:
    """Issue a fixed set of read-only AT queries without exposing raw replies."""

    def __init__(
        self,
        *,
        baudrate: int = 115_200,
        command_timeout_seconds: float = 1.0,
        monotonic=time.monotonic,
    ) -> None:
        self._baudrate = baudrate
        self._command_timeout_seconds = command_timeout_seconds
        self._monotonic = monotonic

    def query(self, port: Path, commands: tuple[str, ...]) -> dict[str, str]:
        if commands != _AT_COMMANDS:
            raise ValueError("AT command set is not allowed")
        if not port.is_absolute() or not _TTY_NAME.fullmatch(port.name):
            raise AtQueryError("AT control port is invalid")
        try:
            details = port.lstat()
        except OSError as error:
            raise AtQueryError("AT control port is unavailable") from error
        if stat.S_ISLNK(details.st_mode) or (
            os.name == "posix" and not stat.S_ISCHR(details.st_mode)
        ):
            raise AtQueryError("AT control port is unsafe")
        try:
            import serial
        except ImportError as error:
            raise AtQueryError("serial support is unavailable") from error

        try:
            connection = serial.Serial(
                str(port),
                baudrate=self._baudrate,
                timeout=0.1,
                write_timeout=0.5,
                exclusive=True,
            )
            with connection:
                replies: dict[str, str] = {}
                for command in commands:
                    connection.reset_input_buffer()
                    connection.write(command.encode("ascii") + b"\r")
                    connection.flush()
                    replies[command] = self._read_reply(connection)
                return replies
        except (OSError, ValueError, serial.SerialException) as error:
            raise AtQueryError("AT control query failed") from error

    def _read_reply(self, connection: object) -> str:
        deadline = self._monotonic() + self._command_timeout_seconds
        payload = bytearray()
        while self._monotonic() < deadline and len(payload) < 4096:
            chunk = connection.read(min(512, 4096 - len(payload)))
            if chunk:
                payload.extend(chunk)
                text = payload.decode("ascii", errors="ignore")
                lines = {line.strip().upper() for line in text.splitlines()}
                if "OK" in lines or "ERROR" in lines or any(
                    line.startswith(("+CME ERROR", "+CMS ERROR")) for line in lines
                ):
                    return text
        raise AtQueryError("AT control query timed out")


class ModemRegistrationProbe:
    def __init__(
        self,
        *,
        inventory: AtControlPortInventory | None = None,
        client: AtCommandClient | None = None,
    ) -> None:
        self._inventory = inventory or SysfsAtControlPortInventory()
        self._client = client or SerialAtCommandClient()

    def probe(self, device: UsbNetworkDevice) -> ModemRegistration:
        ports = [
            port
            for port in self._inventory.ports()
            if port.usb_vid == device.usb_vid
            and port.usb_pid == device.usb_pid
            and (
                device.usb_parent_path is None
                or port.usb_parent_path == device.usb_parent_path
            )
        ]
        if not ports:
            return ModemRegistration(False, "CELLULAR_MODEM_CONTROL_UNAVAILABLE")
        if len(ports) != 1:
            return ModemRegistration(False, "CELLULAR_MODEM_CONTROL_AMBIGUOUS")
        try:
            replies = self._client.query(ports[0].path, _AT_COMMANDS)
        except (AtQueryError, OSError, ValueError):
            return ModemRegistration(False, "CELLULAR_MODEM_CONTROL_UNAVAILABLE")
        if set(replies) != set(_AT_COMMANDS) or not _command_ok(replies["AT"]):
            return ModemRegistration(False, "CELLULAR_MODEM_STATUS_UNAVAILABLE")

        sim = replies["AT+CPIN?"].upper()
        if "+CPIN: READY" not in sim or not _command_ok(sim):
            if "NOT INSERTED" in sim or "SIM ABSENT" in sim:
                return ModemRegistration(False, "CELLULAR_SIM_ABSENT")
            if any(marker in sim for marker in ("SIM PIN", "SIM PUK", "PH-NET")):
                return ModemRegistration(False, "CELLULAR_SIM_LOCKED")
            return ModemRegistration(False, "CELLULAR_MODEM_STATUS_UNAVAILABLE")

        registration = _REGISTRATION.search(replies["AT+CEREG?"])
        if registration is None or not _command_ok(replies["AT+CEREG?"]):
            return ModemRegistration(False, "CELLULAR_MODEM_STATUS_UNAVAILABLE")
        registration_state = registration.group(2) or registration.group(1)
        if registration_state == "3":
            return ModemRegistration(False, "CELLULAR_NETWORK_REGISTRATION_DENIED")
        if registration_state not in {"1", "5"}:
            return ModemRegistration(False, "CELLULAR_NETWORK_REGISTRATION_PENDING")

        attached = _PACKET_ATTACHED.search(replies["AT+CGATT?"])
        if attached is None or not _command_ok(replies["AT+CGATT?"]):
            return ModemRegistration(False, "CELLULAR_MODEM_STATUS_UNAVAILABLE")
        if attached.group(1) != "1":
            return ModemRegistration(False, "CELLULAR_PACKET_SERVICE_PENDING")
        return ModemRegistration(True, "NONE")


def _command_ok(reply: str) -> bool:
    return "OK" in {line.strip().upper() for line in reply.splitlines()}


__all__ = [
    "AtControlPort",
    "AtQueryError",
    "ModemRegistration",
    "ModemRegistrationProbe",
    "SerialAtCommandClient",
    "SysfsAtControlPortInventory",
]
