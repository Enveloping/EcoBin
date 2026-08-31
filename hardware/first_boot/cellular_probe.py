from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import ipaddress
import json
from pathlib import Path
import re
from typing import Callable, Iterable, Protocol

from .cellular_config import CellularBatchConfig
from .command import CommandResult, CommandRunner


_INTERFACE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,31}$")
_USB_ID = re.compile(r"^[0-9a-f]{4}$")


class SimState(StrEnum):
    UNKNOWN = "UNKNOWN"
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LOCKED = "LOCKED"


@dataclass(frozen=True)
class UsbNetworkDevice:
    interface: str
    usb_vid: str
    usb_pid: str
    driver: str
    usb_parent_verified: bool


class UsbNetworkInventory(Protocol):
    def devices(self) -> Iterable[UsbNetworkDevice]: ...


def select_rndis_device(
    config: CellularBatchConfig,
    devices: Iterable[UsbNetworkDevice],
) -> tuple[UsbNetworkDevice | None, str]:
    """Select one capability-compatible USB RNDIS interface.

    USB VID/PID are deliberately not admission inputs.  Procurement batches
    may expose different diagnostic IDs while providing the same locked RNDIS
    data path.  We still fail closed when the USB ancestry is unverified or
    when more than one compatible interface exists, so the coordinator never
    guesses a Linux interface name.
    """

    observed = list(devices)
    compatible = [
        item
        for item in observed
        if item.driver == config.usb_driver and item.usb_parent_verified
    ]
    if len(compatible) == 1:
        return compatible[0], "NONE"
    if len(compatible) > 1:
        return None, "CELLULAR_RNDIS_AMBIGUOUS"
    if any(
        item.driver == config.usb_driver and not item.usb_parent_verified
        for item in observed
    ):
        return None, "CELLULAR_USB_PARENT_UNVERIFIED"
    if observed:
        return None, "CELLULAR_USB_DRIVER_MISMATCH"
    return None, "CELLULAR_RNDIS_UNAVAILABLE"


@dataclass(frozen=True)
class CellularHealth:
    ready: bool
    error_code: str
    interface: str | None = None
    ipv4_address: str | None = None
    usb_vid: str | None = None
    usb_pid: str | None = None
    driver: str | None = None
    dhcp_ready: bool = False
    default_route_ready: bool = False
    dns_ready: bool = False
    https_ready: bool = False
    diagnostic_code: str | None = None
    diagnostic_elapsed_ms: int | None = None


class SysfsUsbNetworkInventory:
    def __init__(self, sys_class_net: Path = Path("/sys/class/net")) -> None:
        self._root = sys_class_net

    def devices(self) -> list[UsbNetworkDevice]:
        result: list[UsbNetworkDevice] = []
        try:
            interfaces = sorted(self._root.iterdir(), key=lambda item: item.name)
        except OSError:
            return result
        for interface_path in interfaces:
            if not _INTERFACE_NAME.fullmatch(interface_path.name):
                continue
            device_link = interface_path / "device"
            try:
                device = device_link.resolve(strict=True)
            except OSError:
                continue
            driver_link = device / "driver"
            try:
                driver = driver_link.resolve(strict=True).name
            except OSError:
                continue
            parent = device
            usb_vid: str | None = None
            usb_pid: str | None = None
            for _ in range(12):
                vid_path = parent / "idVendor"
                pid_path = parent / "idProduct"
                if vid_path.is_file() and pid_path.is_file():
                    try:
                        usb_vid = vid_path.read_text(encoding="ascii").strip().lower()
                        usb_pid = pid_path.read_text(encoding="ascii").strip().lower()
                    except (OSError, UnicodeDecodeError):
                        usb_vid = usb_pid = None
                    break
                if parent.parent == parent:
                    break
                parent = parent.parent
            if (
                usb_vid is None
                or usb_pid is None
                or not _USB_ID.fullmatch(usb_vid)
                or not _USB_ID.fullmatch(usb_pid)
            ):
                continue
            result.append(
                UsbNetworkDevice(
                    interface=interface_path.name,
                    usb_vid=usb_vid,
                    usb_pid=usb_pid,
                    driver=driver,
                    usb_parent_verified=True,
                )
            )
        return result


class CellularProbe:
    def __init__(
        self,
        config: CellularBatchConfig,
        inventory: UsbNetworkInventory,
        *,
        runner: CommandRunner | None = None,
        sim_state: Callable[[str], SimState] | None = None,
    ) -> None:
        self._config = config
        self._inventory = inventory
        self._runner = runner or CommandRunner()
        # Production P5 does not guess SIM state.  A later, explicitly tested
        # read-only AT adapter may supply this port; UNKNOWN does not fabricate
        # a pass or a failure and actual IP probes remain authoritative.
        self._sim_state = sim_state or (lambda _interface: SimState.UNKNOWN)

    def probe(self) -> CellularHealth:
        device, selection_error = select_rndis_device(
            self._config,
            self._inventory.devices(),
        )
        if device is None:
            return CellularHealth(False, selection_error)
        if not _INTERFACE_NAME.fullmatch(device.interface):
            return CellularHealth(False, "CELLULAR_INTERFACE_INVALID")

        sim = self._sim_state(device.interface)
        if sim is SimState.ABSENT:
            return self._base(device, "CELLULAR_SIM_ABSENT")
        if sim is SimState.LOCKED:
            return self._base(device, "CELLULAR_SIM_LOCKED")

        ipv4 = self._ipv4_address(device.interface)
        if ipv4 is None:
            return self._base(device, "CELLULAR_DHCP_UNAVAILABLE")
        if not self._route_is_bound(device.interface):
            return self._base(
                device,
                "CELLULAR_DEFAULT_ROUTE_WRONG_INTERFACE",
                ipv4=ipv4,
                dhcp=True,
            )
        dns_result = self._dns_probe(device.interface)
        if dns_result.return_code != 0:
            return self._base(
                device,
                "CELLULAR_DNS_UNAVAILABLE",
                ipv4=ipv4,
                dhcp=True,
                route=True,
                diagnostic_code=_command_diagnostic("RESOLVECTL", dns_result),
                diagnostic_elapsed_ms=dns_result.elapsed_ms,
            )
        https_result = self._https_probe(device.interface)
        if https_result.return_code != 0:
            return self._base(
                device,
                "CELLULAR_HTTPS_UNAVAILABLE",
                ipv4=ipv4,
                dhcp=True,
                route=True,
                dns=True,
                diagnostic_code=_command_diagnostic("CURL", https_result),
                diagnostic_elapsed_ms=https_result.elapsed_ms,
            )
        return CellularHealth(
            ready=True,
            error_code="NONE",
            interface=device.interface,
            ipv4_address=ipv4,
            usb_vid=device.usb_vid,
            usb_pid=device.usb_pid,
            driver=device.driver,
            dhcp_ready=True,
            default_route_ready=True,
            dns_ready=True,
            https_ready=True,
        )

    def _base(
        self,
        device: UsbNetworkDevice,
        code: str,
        *,
        ipv4: str | None = None,
        dhcp: bool = False,
        route: bool = False,
        dns: bool = False,
        diagnostic_code: str | None = None,
        diagnostic_elapsed_ms: int | None = None,
    ) -> CellularHealth:
        return CellularHealth(
            False,
            code,
            interface=device.interface,
            ipv4_address=ipv4,
            usb_vid=device.usb_vid,
            usb_pid=device.usb_pid,
            driver=device.driver,
            dhcp_ready=dhcp,
            default_route_ready=route,
            dns_ready=dns,
            diagnostic_code=diagnostic_code,
            diagnostic_elapsed_ms=diagnostic_elapsed_ms,
        )

    def _ipv4_address(self, interface: str) -> str | None:
        result = self._run(("/usr/sbin/ip", "-j", "-4", "address", "show", "dev", interface))
        if result.return_code != 0:
            return None
        try:
            documents = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        if not isinstance(documents, list):
            return None
        for document in documents:
            if not isinstance(document, dict) or document.get("ifname") != interface:
                continue
            addresses = document.get("addr_info", [])
            if not isinstance(addresses, list):
                continue
            for address in addresses:
                if not isinstance(address, dict) or address.get("scope") != "global":
                    continue
                try:
                    parsed = ipaddress.IPv4Address(address.get("local"))
                except ipaddress.AddressValueError:
                    continue
                if not parsed.is_loopback and not parsed.is_unspecified:
                    return str(parsed)
        return None

    def _route_is_bound(self, interface: str) -> bool:
        defaults = self._run(("/usr/sbin/ip", "-j", "-4", "route", "show", "default"))
        route_get = self._run(
            ("/usr/sbin/ip", "-j", "-4", "route", "get", self._config.probe_ipv4)
        )
        if defaults.return_code != 0 or route_get.return_code != 0:
            return False
        try:
            default_rows = json.loads(defaults.stdout)
            selected_rows = json.loads(route_get.stdout)
        except json.JSONDecodeError:
            return False
        if not isinstance(default_rows, list) or not isinstance(selected_rows, list):
            return False
        has_bound_default = any(
            isinstance(row, dict)
            and row.get("dst", "default") == "default"
            and row.get("dev") == interface
            for row in default_rows
        )
        selected_bound = bool(selected_rows) and all(
            isinstance(row, dict) and row.get("dev") == interface
            for row in selected_rows
        )
        return has_bound_default and selected_bound

    def _dns_probe(self, interface: str) -> CommandResult:
        return self._run(
            (
                "/usr/bin/resolvectl",
                "query",
                f"--interface={interface}",
                "--legend=no",
                "--type=A",
                self._config.https_probe_host,
            )
        )

    def _https_probe(self, interface: str) -> CommandResult:
        return self._run(
            (
                "/usr/bin/curl",
                "--fail",
                "--silent",
                "--show-error",
                "--connect-timeout",
                "5",
                "--max-time",
                "10",
                "--interface",
                interface,
                "--output",
                "/dev/null",
                self._config.https_probe_url,
            ),
            timeout=12,
        )

    def _run(self, argv: tuple[str, ...], *, timeout: float = 5) -> CommandResult:
        return self._runner.run(argv, timeout_seconds=timeout)


def _command_diagnostic(command: str, result: CommandResult) -> str:
    if result.failure_kind is not None:
        return f"{command}_{result.failure_kind}"
    if result.return_code < 0:
        return f"{command}_SIGNAL_{-result.return_code}"
    return f"{command}_EXIT_{result.return_code}"
