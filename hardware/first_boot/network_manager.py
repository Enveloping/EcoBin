from __future__ import annotations

import os
from pathlib import Path
import re
import uuid

from .atomic_json import AtomicWriteError
from .cellular_config import CellularBatchConfig
from .cellular_probe import UsbNetworkDevice
from .command import CommandRunner


_SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


def render_network_manager_profile(
    config: CellularBatchConfig,
    device: UsbNetworkDevice,
) -> str:
    if (
        not device.usb_parent_verified
        or device.driver != config.usb_driver
    ):
        raise ValueError("CELLULAR_PROFILE_DEVICE_MISMATCH")
    if not _SAFE_VALUE.fullmatch(device.interface):
        raise ValueError("CELLULAR_INTERFACE_INVALID")
    connection_uuid = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"ecobin:air780e:rndis-v2:{config.connection_id}",
    )
    return (
        "[connection]\n"
        f"id={config.connection_id}\n"
        f"uuid={connection_uuid}\n"
        "type=ethernet\n"
        f"interface-name={device.interface}\n"
        "autoconnect=false\n"
        "autoconnect-retries=0\n"
        "multi-connect=0\n"
        "\n"
        "[ethernet]\n"
        "auto-negotiate=true\n"
        "\n"
        "[ipv4]\n"
        "method=auto\n"
        "never-default=false\n"
        "route-metric=50\n"
        "\n"
        "[ipv6]\n"
        "method=disabled\n"
        "\n"
        "[proxy]\n"
    )


def install_network_manager_profile(path: Path, content: str) -> None:
    encoded = content.encode("utf-8")
    if not 1 <= len(encoded) <= 16 * 1024:
        raise ValueError("CELLULAR_PROFILE_SIZE_INVALID")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = -1
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception as error:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        if isinstance(error, ValueError):
            raise
        raise AtomicWriteError("cellular profile write failed") from error


class NetworkManagerActivator:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or CommandRunner()

    def activate(
        self,
        *,
        profile_path: Path,
        connection_id: str,
        interface: str,
        factory_test_passed: bool,
        factory_recovery_required: bool,
    ) -> bool:
        if not factory_test_passed or factory_recovery_required:
            raise PermissionError("FACTORY_TEST_GATE_CLOSED")
        if not _SAFE_VALUE.fullmatch(connection_id) or not _SAFE_VALUE.fullmatch(interface):
            raise ValueError("CELLULAR_ACTIVATION_ARGUMENT_INVALID")
        loaded = self._runner.run(
            ("/usr/bin/nmcli", "connection", "load", str(profile_path)),
            timeout_seconds=10,
        )
        if loaded.return_code != 0:
            return False
        activated = self._runner.run(
            (
                "/usr/bin/nmcli",
                "connection",
                "up",
                "id",
                connection_id,
                "ifname",
                interface,
            ),
            timeout_seconds=30,
        )
        return activated.return_code == 0
