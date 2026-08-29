"""Atomic nftables profiles locked to the selected Air780E RNDIS interface."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Callable, Sequence

try:
    import pwd
except ImportError:  # pragma: no cover - production target is Linux
    pwd = None

from device_credentials import load_remote_support_credentials

from factory.firewall import (
    EMERGENCY_RULE_MARKERS,
    FILTER_PRIORITY,
    NFT_PATH,
    NFT_TABLE,
    NftApplyError,
    _invoke,
    _restore_emergency,
    _rule,
    _table_exists,
    _table_prefix,
    _verify_profile,
)


_INTERFACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,31}$")
REMOTE_SUPPORT_USER = "ecobin-remote"
REMOTE_SUPPORT_RULE_MARKER = "ecobin-cellular:remote-support-output"
FIREWALL_TRANSITION_LOCK = Path(
    "/run/lock/ecobin/cellular-firewall.lock"
)
CELLULAR_RULE_MARKERS = frozenset(
    {
        "ecobin-cellular:dns-udp-input",
        "ecobin-cellular:ntp-input",
        "ecobin-cellular:invalid-input",
        "ecobin-cellular:invalid-output",
        "ecobin-cellular:dhcp-output",
        "ecobin-cellular:dhcp-input",
        "ecobin-cellular:dns-udp-output",
        "ecobin-cellular:ntp-output",
        "ecobin-cellular:tcp-output",
        "ecobin-cellular:established-input",
    }
)
FACTORY_UPLINK_RULE_MARKERS = EMERGENCY_RULE_MARKERS | CELLULAR_RULE_MARKERS | frozenset(
    {
        "ecobin-cellular:factory-dhcp-input",
        "ecobin-cellular:factory-dhcp-output",
        "ecobin-cellular:factory-dns-udp-input",
        "ecobin-cellular:factory-dns-udp-output",
        "ecobin-cellular:factory-dns-tcp-input",
        "ecobin-cellular:factory-dns-tcp-output",
        "ecobin-cellular:factory-http-input",
        "ecobin-cellular:factory-http-output",
    }
)
PRODUCTION_UPLINK_RULE_MARKERS = EMERGENCY_RULE_MARKERS | CELLULAR_RULE_MARKERS


def _validate_interface(interface: str) -> str:
    if not _INTERFACE.fullmatch(interface) or interface == "wlan0":
        raise ValueError("CELLULAR_INTERFACE_INVALID")
    return interface


def _validate_remote_support_egress(
    uid: int | None,
    port: int | None,
) -> tuple[int, int] | None:
    if uid is None and port is None:
        return None
    if uid is None or port is None:
        raise ValueError("REMOTE_SUPPORT_EGRESS_INCOMPLETE")
    if isinstance(uid, bool) or not isinstance(uid, int) or not 1 <= uid < 2**32:
        raise ValueError("REMOTE_SUPPORT_UID_INVALID")
    if (
        isinstance(port, bool)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
    ):
        raise ValueError("REMOTE_SUPPORT_PORT_INVALID")
    return uid, port


def discover_remote_support_egress(
    *,
    credentials_loader=load_remote_support_credentials,
    user_lookup=None,
) -> tuple[int, int] | None:
    """Return the dedicated tunnel account and configured SSH port if ready.

    Before enrollment neither the projected credential nor the service account
    is guaranteed to exist.  Omitting the exception in that state preserves the
    factory fail-closed boundary while the next cellular reconciliation adds it
    after enrollment has installed both facts.
    """

    if user_lookup is None:
        if pwd is None:
            return None
        user_lookup = pwd.getpwnam
    try:
        credentials = credentials_loader()
        if credentials is None:
            return None
        account = user_lookup(REMOTE_SUPPORT_USER)
        return _validate_remote_support_egress(
            int(account.pw_uid),
            credentials.server_port,
        )
    except (KeyError, OSError, TypeError, ValueError):
        # Invalid or incomplete remote-maintenance setup must never broaden
        # egress and must not take unrelated MQTT/COS traffic offline.
        return None


def _cellular_rules(
    interface: str,
    *,
    remote_support_uid: int | None = None,
    remote_support_port: int | None = None,
) -> list[str]:
    remote_support_egress = _validate_remote_support_egress(
        remote_support_uid,
        remote_support_port,
    )
    rules = [
        # The qualified H616 kernel does not consistently classify Air780E
        # UDP DNS replies as established.  Keep the exception narrow and
        # ahead of the invalid-state drop: only source port 53 on the one
        # USB-parent-verified RNDIS interface is admitted.
        _rule(
            "input",
            f'iifname "{interface}" udp sport 53 accept',
            "ecobin-cellular:dns-udp-input",
        ),
        _rule(
            "input",
            f'iifname "{interface}" udp sport 123 accept',
            "ecobin-cellular:ntp-input",
        ),
        _rule("input", "ct state invalid drop", "ecobin-cellular:invalid-input"),
        _rule(
            "output",
            f'oifname "{interface}" udp sport 68 udp dport 67 accept',
            "ecobin-cellular:dhcp-output",
        ),
        _rule(
            "input",
            f'iifname "{interface}" udp sport 67 udp dport 68 accept',
            "ecobin-cellular:dhcp-input",
        ),
        _rule(
            "output",
            f'oifname "{interface}" udp dport 53 accept',
            "ecobin-cellular:dns-udp-output",
        ),
        _rule(
            "output",
            f'oifname "{interface}" udp dport 123 accept',
            "ecobin-cellular:ntp-output",
        ),
    ]
    if remote_support_egress is not None:
        uid, port = remote_support_egress
        # The qualified H616/Air780E path can classify a new SSH SYN as
        # invalid.  Keep this account- and port-scoped exception ahead of the
        # invalid-state drop, just like the qualified DNS/NTP reply rules.
        rules.append(
            _rule(
                "output",
                (
                    f'oifname "{interface}" meta skuid {uid} '
                    f"tcp dport {port} accept"
                ),
                REMOTE_SUPPORT_RULE_MARKER,
            )
        )
    rules.append(
        _rule("output", "ct state invalid drop", "ecobin-cellular:invalid-output")
    )
    rules.extend(
        [
            _rule(
                "output",
                (
                    f'oifname "{interface}" tcp dport '
                    "{ 53, 443, 1883, 8883 } accept"
                ),
                "ecobin-cellular:tcp-output",
            ),
            _rule(
                "input",
                f'iifname "{interface}" ct state established,related accept',
                "ecobin-cellular:established-input",
            ),
        ]
    )
    return rules


def _base_rules(
    interface: str,
    *,
    keep_factory_portal: bool,
    table_exists: bool,
    remote_support_uid: int | None,
    remote_support_port: int | None,
) -> str:
    interface = _validate_interface(interface)
    lines = _table_prefix(table_exists)
    lines.extend(
        (
            _rule("input", 'iifname "lo" accept', "ecobin-factory:loopback-input"),
            _rule("output", 'oifname "lo" accept', "ecobin-factory:loopback-output"),
        )
    )
    if keep_factory_portal:
        lines.extend(
            (
                _rule(
                    "input",
                    'iifname "wlan0" udp sport 68 udp dport 67 accept',
                    "ecobin-cellular:factory-dhcp-input",
                ),
                _rule(
                    "output",
                    'oifname "wlan0" udp sport 67 udp dport 68 accept',
                    "ecobin-cellular:factory-dhcp-output",
                ),
                _rule(
                    "input",
                    'iifname "wlan0" ip saddr 10.42.0.0/24 ip daddr 10.42.0.1 udp dport 53 accept',
                    "ecobin-cellular:factory-dns-udp-input",
                ),
                _rule(
                    "output",
                    'oifname "wlan0" ip saddr 10.42.0.1 ip daddr 10.42.0.0/24 udp sport 53 accept',
                    "ecobin-cellular:factory-dns-udp-output",
                ),
                _rule(
                    "input",
                    'iifname "wlan0" ip saddr 10.42.0.0/24 ip daddr 10.42.0.1 tcp dport 53 accept',
                    "ecobin-cellular:factory-dns-tcp-input",
                ),
                _rule(
                    "output",
                    'oifname "wlan0" ip saddr 10.42.0.1 ip daddr 10.42.0.0/24 tcp sport 53 ct state established accept',
                    "ecobin-cellular:factory-dns-tcp-output",
                ),
                _rule(
                    "input",
                    'iifname "wlan0" ip saddr 10.42.0.0/24 ip daddr 10.42.0.1 tcp dport 80 accept',
                    "ecobin-cellular:factory-http-input",
                ),
                _rule(
                    "output",
                    'oifname "wlan0" ip saddr 10.42.0.1 ip daddr 10.42.0.0/24 tcp sport 80 ct state established accept',
                    "ecobin-cellular:factory-http-output",
                ),
            )
        )
    lines.extend(
        _cellular_rules(
            interface,
            remote_support_uid=remote_support_uid,
            remote_support_port=remote_support_port,
        )
    )
    return "\n".join(lines) + "\n"


def render_factory_uplink_gate(
    interface: str,
    *,
    remote_support_uid: int | None = None,
    remote_support_port: int | None = None,
    table_exists: bool = True,
) -> str:
    """Keep AP local while allowing device-originated traffic only on RNDIS."""

    return _base_rules(
        interface,
        keep_factory_portal=True,
        table_exists=table_exists,
        remote_support_uid=remote_support_uid,
        remote_support_port=remote_support_port,
    )


def render_production_uplink_gate(
    interface: str,
    *,
    remote_support_uid: int | None = None,
    remote_support_port: int | None = None,
    table_exists: bool = True,
) -> str:
    """After SEALED, remove AP access but retain the same strict RNDIS egress."""

    return _base_rules(
        interface,
        keep_factory_portal=False,
        table_exists=table_exists,
        remote_support_uid=remote_support_uid,
        remote_support_port=remote_support_port,
    )


Runner = Callable[[Sequence[str], bytes], subprocess.CompletedProcess[bytes]]
SealInspector = Callable[[], object]


def _default_runner(
    command: Sequence[str], standard_input: bytes
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        shell=False,
        input=standard_input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=5,
    )


@contextmanager
def _transition_lock(path: Path):
    """Serialize profile selection and replacement across root processes."""

    if not path.is_absolute():
        raise ValueError("CELLULAR_FIREWALL_LOCK_PATH_INVALID")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if os.name != "nt":
            import fcntl

            deadline = time.monotonic() + 5.0
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("cellular firewall transition lock busy")
                    time.sleep(0.01)
        yield
    finally:
        if os.name != "nt":
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(descriptor)


def _apply_locked_profile(
    interface: str,
    *,
    keep_factory_portal: bool,
    runner: Runner | None,
) -> bool:
    execute = runner or _default_runner
    expected = (
        FACTORY_UPLINK_RULE_MARKERS
        if keep_factory_portal
        else PRODUCTION_UPLINK_RULE_MARKERS
    )
    remote_support_egress = discover_remote_support_egress()
    if remote_support_egress is not None:
        expected = expected | frozenset({REMOTE_SUPPORT_RULE_MARKER})
    renderer = (
        render_factory_uplink_gate
        if keep_factory_portal
        else render_production_uplink_gate
    )

    def fail_closed() -> bool:
        try:
            _restore_emergency(execute)
            _verify_profile(execute, EMERGENCY_RULE_MARKERS)
        except (NftApplyError, OSError, subprocess.SubprocessError):
            pass
        return False

    try:
        # The unconditional early egress lock owns this table.  Missing it is
        # a safety failure, not permission to create an unproven transition.
        if not _table_exists(execute):
            return fail_closed()
        remote_support_uid = (
            remote_support_egress[0]
            if remote_support_egress is not None
            else None
        )
        remote_support_port = (
            remote_support_egress[1]
            if remote_support_egress is not None
            else None
        )
        rules = renderer(
            interface,
            table_exists=True,
            remote_support_uid=remote_support_uid,
            remote_support_port=remote_support_port,
        )
        checked = _invoke(("-c", "-f", "-"), rules, execute)
        if checked.returncode != 0:
            return fail_closed()
        loaded = _invoke(("-f", "-"), rules, execute)
        if loaded.returncode != 0:
            return fail_closed()
        _verify_profile(execute, expected)
        return True
    except (NftApplyError, OSError, subprocess.SubprocessError):
        return fail_closed()


def apply_factory_uplink_gate(
    interface: str,
    *,
    runner: Runner | None = None,
    lock_path: Path = FIREWALL_TRANSITION_LOCK,
) -> bool:
    try:
        with _transition_lock(lock_path):
            return _apply_locked_profile(
                interface,
                keep_factory_portal=True,
                runner=runner,
            )
    except (OSError, TimeoutError, ValueError):
        return False


def apply_production_uplink_gate(
    interface: str,
    *,
    runner: Runner | None = None,
    lock_path: Path = FIREWALL_TRANSITION_LOCK,
) -> bool:
    try:
        with _transition_lock(lock_path):
            return _apply_locked_profile(
                interface,
                keep_factory_portal=False,
                runner=runner,
            )
    except (OSError, TimeoutError, ValueError):
        return False


def _apply_emergency(execute: Runner) -> bool:
    try:
        _restore_emergency(execute)
        _verify_profile(execute, EMERGENCY_RULE_MARKERS)
        return True
    except (NftApplyError, OSError, subprocess.SubprocessError):
        return False


def apply_emergency_uplink_lock(
    *,
    runner: Runner | None = None,
    lock_path: Path = FIREWALL_TRANSITION_LOCK,
) -> bool:
    """Public fail-closed entry for selection/configuration failures."""

    execute = runner or _default_runner
    try:
        with _transition_lock(lock_path):
            return _apply_emergency(execute)
    except (OSError, TimeoutError, ValueError):
        return False


def apply_seal_aware_uplink_gate(
    interface: str,
    inspect_seal: SealInspector,
    *,
    runner: Runner | None = None,
    lock_path: Path = FIREWALL_TRANSITION_LOCK,
) -> str:
    """Choose factory/production while holding the cross-process nft lock.

    The inspector deliberately runs *inside* the same lock used by the P8
    production transition.  Therefore an old unsealed observation can never
    overwrite a newer production profile after sealing.
    """

    execute = runner or _default_runner
    try:
        with _transition_lock(lock_path):
            fact = inspect_seal()
            exists = getattr(fact, "exists", None)
            valid = getattr(fact, "valid", None)
            if type(exists) is not bool or type(valid) is not bool:
                _apply_emergency(execute)
                return "SEALED_FACT_INVALID"
            if exists and not valid:
                _apply_emergency(execute)
                return "SEALED_FACT_INVALID"
            applied = _apply_locked_profile(
                interface,
                keep_factory_portal=not valid,
                runner=execute,
            )
            if not applied:
                return "CELLULAR_EGRESS_GATE_FAILED"
            return "PRODUCTION" if valid else "FACTORY"
    except (OSError, TimeoutError, ValueError):
        # If the serialization primitive itself is unavailable, never attempt
        # a candidate profile outside it.  The unconditional early boot lock
        # remains the only trusted policy.
        return "CELLULAR_EGRESS_GATE_FAILED"
