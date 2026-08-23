"""Prepare and monitor the isolated factory access point.

Root runs only the idempotent ``prepare``/``cleanup`` commands.  hostapd and
dnsmasq are separate systemd services running as dedicated users.  The
long-lived ``monitor`` command has no capabilities; it reports READY only
after wlan0 is in AP mode, owns the fixed address and dnsmasq accepts TCP DNS.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import time
from typing import Any

try:  # POSIX-only modules; pure rendering tests also run on Windows.
    import grp
    import pwd
except ImportError:  # pragma: no cover - exercised by Windows import itself
    grp = None  # type: ignore[assignment]
    pwd = None  # type: ignore[assignment]

from .network import (
    FACTORY_ADDRESS,
    FACTORY_INTERFACE,
    FACTORY_PREFIX_LENGTH,
    FactoryNetworkConfig,
    FactoryNetworkConfigurationError,
    derive_factory_id,
    read_setup_ap_passphrase,
    render_dnsmasq,
    render_hostapd,
)
from factory_seal.validation import (
    FactorySealPaths,
    inspect_sealed_authorization,
)
from first_boot.status_projection import validate_ap_authorization_projection


MACHINE_ID_PATH = Path("/etc/machine-id")
IMAGE_RELEASE_PATH = Path("/etc/ecobin/image-release.json")
SETUP_AP_KEY_PATH = Path("/etc/ecobin/setup-ap.key")
SEALED_PATH = Path("/var/lib/ecobin/first-boot/sealed.json")
AP_EDGE_STORE_PATH = Path(
    "/run/ecobin/factory-network/edge-store/edge.db"
)
RUNTIME_DIRECTORY = Path("/run/ecobin/factory-network")
AP_AUTHORIZATION_PATH = RUNTIME_DIRECTORY / "ap-allowed.json"
HOSTAPD_DIRECTORY = RUNTIME_DIRECTORY / "hostapd"
DNSMASQ_DIRECTORY = RUNTIME_DIRECTORY / "dnsmasq"
DNSMASQ_STATE_DIRECTORY = RUNTIME_DIRECTORY / "dnsmasq-state"
HOSTAPD_CONFIG_PATH = HOSTAPD_DIRECTORY / "hostapd.conf"
DNSMASQ_CONFIG_PATH = DNSMASQ_DIRECTORY / "dnsmasq.conf"
DNSMASQ_LEASE_PATH = DNSMASQ_STATE_DIRECTORY / "dnsmasq.leases"

HOSTAPD_ACCOUNT = "ecobin-factory-ap"
DNSMASQ_ACCOUNT = "ecobin-factory-dns"

IP_PATH = "/usr/sbin/ip"
IW_PATH = "/usr/sbin/iw"
SYSCTL_PATH = "/usr/sbin/sysctl"

READINESS_TIMEOUT_SECONDS = 20.0
READINESS_STABLE_SAMPLES = 3
HEALTH_INTERVAL_SECONDS = 2.0


class AccessPointStartupError(RuntimeError):
    """Raised when the AP cannot be brought up without weakening isolation."""


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    uid: int
    gid: int


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]]
TcpProbe = Callable[[str, int, float], None]


def _read_bounded_text(path: Path, maximum_bytes: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AccessPointStartupError(f"required factory file is unavailable: {path}") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size > maximum_bytes:
            raise AccessPointStartupError(f"invalid factory file: {path}")
        raw = os.read(descriptor, maximum_bytes + 1)
    finally:
        os.close(descriptor)
    if len(raw) > maximum_bytes:
        raise AccessPointStartupError(f"factory file exceeds size limit: {path}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AccessPointStartupError(f"factory file is not UTF-8: {path}") from exc


def _image_release_id(path: Path) -> str:
    try:
        document: Any = json.loads(_read_bounded_text(path, 64 * 1024))
    except (json.JSONDecodeError, TypeError) as exc:
        raise AccessPointStartupError("image release manifest is invalid") from exc
    if not isinstance(document, dict):
        raise AccessPointStartupError("image release manifest must be an object")
    value = document.get("releaseId", document.get("imageReleaseId"))
    if not isinstance(value, str) or not value.strip():
        raise AccessPointStartupError("image release id is missing")
    return value.strip()


def _lookup_identity(account: str) -> RuntimeIdentity:
    if pwd is None or grp is None:
        raise AccessPointStartupError("factory service accounts require POSIX")
    try:
        user = pwd.getpwnam(account)
        group = grp.getgrnam(account)
    except KeyError as exc:
        raise AccessPointStartupError("required factory service account is unavailable") from exc
    return RuntimeIdentity(user.pw_uid, group.gr_gid)


def _current_identity() -> RuntimeIdentity:
    return RuntimeIdentity(getattr(os, "geteuid", lambda: 0)(), getattr(os, "getegid", lambda: 0)())


def _ensure_directory(path: Path, mode: int, identity: RuntimeIdentity) -> None:
    path.mkdir(mode=mode, parents=True, exist_ok=True)
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise AccessPointStartupError("factory runtime path is not a real directory")
    if os.name == "posix":
        # The root preparation unit deliberately has CAP_CHOWN but neither
        # CAP_DAC_OVERRIDE nor CAP_FOWNER.  Reclaim an existing directory before
        # chmod so an idempotent restart can safely prepare a directory that was
        # handed to a dedicated daemon during the previous successful run.
        preparer = _current_identity()
        if details.st_uid != preparer.uid:
            os.chown(path, preparer.uid, preparer.gid)
    os.chmod(path, mode)
    if os.name == "posix":
        os.chown(path, identity.uid, identity.gid)


def _atomic_write(
    path: Path,
    content: str,
    mode: int,
    identity: RuntimeIdentity,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, mode)
        else:  # Windows-only test support; production is POSIX.
            os.chmod(temporary, mode)
        if os.name == "posix":
            os.fchown(descriptor, identity.uid, identity.gid)
        payload = content.encode("utf-8")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while creating factory configuration")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def assert_factory_unsealed(
    path: Path = SEALED_PATH,
    edge_store_path: Path = AP_EDGE_STORE_PATH,
) -> None:
    """Root prepare gate: validate marker *and* SQLite authorization state."""

    fact = inspect_sealed_authorization(
        FactorySealPaths(sealed=path, edge_store=edge_store_path)
    )
    if fact.exists:
        raise AccessPointStartupError(
            f"factory access point is disabled after sealing: {fact.status_code}"
        )


def assert_factory_ap_projection_allowed(
    path: Path = AP_AUTHORIZATION_PATH,
) -> None:
    """Low-privilege monitor reads only the root-owned minimal projection."""

    try:
        value: Any = json.loads(_read_bounded_text(path, 512))
        projection = validate_ap_authorization_projection(value)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AccessPointStartupError(
            "factory AP authorization projection is invalid"
        ) from exc
    if projection["allowed"] is not True:
        raise AccessPointStartupError("factory access point is no longer allowed")


def prepare_runtime_configuration(
    *,
    machine_id_path: Path = MACHINE_ID_PATH,
    image_release_path: Path = IMAGE_RELEASE_PATH,
    setup_ap_key_path: Path = SETUP_AP_KEY_PATH,
    runtime_directory: Path = RUNTIME_DIRECTORY,
    hostapd_identity: RuntimeIdentity | None = None,
    dnsmasq_identity: RuntimeIdentity | None = None,
) -> FactoryNetworkConfig:
    machine_id = _read_bounded_text(machine_id_path, 128).strip()
    release_id = _image_release_id(image_release_path)
    config = FactoryNetworkConfig(factory_id=derive_factory_id(machine_id, release_id))
    passphrase = read_setup_ap_passphrase(setup_ap_key_path)

    host_identity = hostapd_identity or _current_identity()
    dns_identity = dnsmasq_identity or _current_identity()
    preparer_identity = _current_identity()
    dns_config_identity = RuntimeIdentity(preparer_identity.uid, dns_identity.gid)
    host_directory = runtime_directory / "hostapd"
    dns_directory = runtime_directory / "dnsmasq"
    dns_state_directory = runtime_directory / "dnsmasq-state"
    _ensure_directory(host_directory, 0o750, host_identity)
    _ensure_directory(dns_directory, 0o750, dns_config_identity)
    # Keep the 0700 state directory owned by this short-lived preparer while it
    # creates/replaces the lease file.  Only then hand the directory to dnsmasq;
    # otherwise a capability-minimized UID 0 cannot create the mkstemp file.
    _ensure_directory(dns_state_directory, 0o700, preparer_identity)
    _atomic_write(
        host_directory / HOSTAPD_CONFIG_PATH.name,
        render_hostapd(config, passphrase),
        0o640,
        host_identity,
    )
    _atomic_write(
        dns_directory / DNSMASQ_CONFIG_PATH.name,
        render_dnsmasq(config),
        0o640,
        dns_config_identity,
    )
    _atomic_write(
        dns_state_directory / DNSMASQ_LEASE_PATH.name,
        "",
        0o600,
        dns_identity,
    )
    _ensure_directory(dns_state_directory, 0o700, dns_identity)
    return config


def _default_command_runner(command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )


def _run_checked(command: tuple[str, ...], runner: CommandRunner) -> bytes:
    completed = runner(command)
    if completed.returncode != 0:
        raise AccessPointStartupError(
            f"fixed access-point command failed: {Path(command[0]).name}"
        )
    return completed.stdout


def configure_interface(
    config: FactoryNetworkConfig,
    *,
    runner: CommandRunner = _default_command_runner,
) -> None:
    """Detach wlan0 from any bridge and disable all kernel IP forwarding."""

    _run_checked(
        (
            SYSCTL_PATH,
            "-q",
            "-w",
            "net.ipv4.ip_forward=0",
            "net.ipv6.conf.all.forwarding=0",
            "net.ipv6.conf.default.forwarding=0",
            f"net.ipv6.conf.{config.interface}.disable_ipv6=1",
        ),
        runner,
    )
    _run_checked((IP_PATH, "link", "set", "dev", config.interface, "down"), runner)
    _run_checked((IP_PATH, "link", "set", "dev", config.interface, "nomaster"), runner)
    _run_checked((IP_PATH, "address", "flush", "dev", config.interface), runner)
    _run_checked(
        (
            IP_PATH,
            "address",
            "add",
            f"{config.address}/{config.prefix_length}",
            "dev",
            config.interface,
        ),
        runner,
    )
    _run_checked((IP_PATH, "link", "set", "dev", config.interface, "up"), runner)


def prepare_access_point() -> None:
    if getattr(os, "geteuid", lambda: 1)() != 0:
        raise AccessPointStartupError("factory AP preparation requires root")
    assert_factory_unsealed()
    config = prepare_runtime_configuration(
        hostapd_identity=RuntimeIdentity(0, _lookup_identity(HOSTAPD_ACCOUNT).gid),
        dnsmasq_identity=_lookup_identity(DNSMASQ_ACCOUNT),
    )
    configure_interface(config)


def cleanup_interface(*, runner: CommandRunner = _default_command_runner) -> None:
    _run_checked((IP_PATH, "link", "set", "dev", FACTORY_INTERFACE, "down"), runner)


def _default_tcp_probe(address: str, port: int, timeout: float) -> None:
    with socket.create_connection((address, port), timeout=timeout):
        return


def access_point_is_ready(
    *,
    runner: CommandRunner = _default_command_runner,
    tcp_probe: TcpProbe = _default_tcp_probe,
) -> bool:
    try:
        address_output = _run_checked(
            (IP_PATH, "-j", "address", "show", "dev", FACTORY_INTERFACE), runner
        )
        document: Any = json.loads(address_output.decode("utf-8"))
        if not isinstance(document, list) or len(document) != 1:
            return False
        interface = document[0]
        if not isinstance(interface, dict) or "UP" not in interface.get("flags", []):
            return False
        addresses = interface.get("addr_info")
        if not isinstance(addresses, list) or not any(
            isinstance(item, dict)
            and item.get("family") == "inet"
            and item.get("local") == FACTORY_ADDRESS
            and item.get("prefixlen") == FACTORY_PREFIX_LENGTH
            for item in addresses
        ):
            return False

        wireless = _run_checked((IW_PATH, "dev", FACTORY_INTERFACE, "info"), runner)
        if re.search(rb"(?m)^\s*type\s+AP\s*$", wireless) is None:
            return False
        tcp_probe(FACTORY_ADDRESS, 53, 1.0)
        return True
    except (
        AccessPointStartupError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,
        subprocess.SubprocessError,
    ):
        return False


def _systemd_notify(message: str) -> None:
    target = os.environ.get("NOTIFY_SOCKET")
    if not target:
        raise AccessPointStartupError("systemd notify socket is unavailable")
    address: str | bytes = target
    if target.startswith("@"):
        address = b"\0" + target[1:].encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.connect(address)
        client.sendall(message.encode("utf-8"))


def monitor_access_point() -> None:
    assert_factory_ap_projection_allowed()
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    stable = 0
    while time.monotonic() < deadline:
        if access_point_is_ready():
            stable += 1
            if stable >= READINESS_STABLE_SAMPLES:
                break
        else:
            stable = 0
        time.sleep(0.25)
    else:
        raise AccessPointStartupError("factory AP did not become ready")

    _systemd_notify("READY=1\nSTATUS=Factory AP and local DNS are ready")
    while True:
        time.sleep(HEALTH_INTERVAL_SECONDS)
        assert_factory_ap_projection_allowed()
        if not access_point_is_ready():
            raise AccessPointStartupError("factory AP readiness was lost")
        _systemd_notify("WATCHDOG=1")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EcoBin isolated factory AP controller")
    parser.add_argument("action", choices=("prepare", "cleanup", "monitor"))
    args = parser.parse_args(arguments)
    try:
        if args.action == "prepare":
            prepare_access_point()
        elif args.action == "cleanup":
            cleanup_interface()
        else:
            monitor_access_point()
    except (
        AccessPointStartupError,
        FactoryNetworkConfigurationError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        # Never print generated configuration, child output, or the passphrase.
        print(f"factory access point unavailable: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
