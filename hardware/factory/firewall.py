"""Install and prove the early, whole-machine factory egress lock.

Debian 12 ships nftables 1.0.6.  That version has atomic ``nft -f``
transactions, ``delete table`` and JSON listing, but does not yet have the
idempotent ``destroy table`` command.  We therefore discover whether our
private table exists, delete it when necessary, recreate it in one
transaction, and then verify the resulting hooks, policies and rule markers
from ``nft -j`` output.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import subprocess
from typing import Any

from .network import FACTORY_ADDRESS, FACTORY_INTERFACE, FACTORY_PORT


NFT_PATH = "/usr/sbin/nft"
NFT_TABLE = "ecobin_factory"
FILTER_PRIORITY = -150  # after conntrack (-200), before normal filter chains

EMERGENCY_RULE_MARKERS = frozenset(
    {
        "ecobin-factory:loopback-input",
        "ecobin-factory:loopback-output",
    }
)
FACTORY_RULE_MARKERS = EMERGENCY_RULE_MARKERS | frozenset(
    {
        "ecobin-factory:invalid-input",
        "ecobin-factory:invalid-output",
        "ecobin-factory:dhcp-discover-input",
        "ecobin-factory:dhcp-renew-input",
        "ecobin-factory:dhcp-rebind-input",
        "ecobin-factory:dhcp-broadcast-output",
        "ecobin-factory:dhcp-unicast-output",
        "ecobin-factory:dns-udp-input",
        "ecobin-factory:dns-udp-output",
        "ecobin-factory:dns-tcp-input",
        "ecobin-factory:dns-tcp-output",
        "ecobin-factory:http-input",
        "ecobin-factory:http-output",
    }
)
class NftApplyError(RuntimeError):
    """Raised when the machine cannot be proven to have the factory lock."""


def _table_prefix(table_exists: bool) -> list[str]:
    lines: list[str] = []
    if table_exists:
        lines.append(f"delete table inet {NFT_TABLE}")
    lines.append(f"add table inet {NFT_TABLE}")
    lines.extend(
        f"add chain inet {NFT_TABLE} {name} "
        f"{{ type filter hook {name} priority {FILTER_PRIORITY}; policy drop; }}"
        for name in ("input", "output", "forward")
    )
    return lines


def _rule(chain: str, expression: str, marker: str) -> str:
    return (
        f"add rule inet {NFT_TABLE} {chain} {expression} "
        f'comment "{marker}"'
    )


def render_emergency_lock(*, table_exists: bool = True) -> str:
    """Return a canonical drop-all lock which permits only loopback."""

    lines = _table_prefix(table_exists)
    lines.extend(
        (
            _rule("input", 'iifname "lo" accept', "ecobin-factory:loopback-input"),
            _rule("output", 'oifname "lo" accept', "ecobin-factory:loopback-output"),
        )
    )
    return "\n".join(lines) + "\n"


def render_factory_egress_lock(*, table_exists: bool = True) -> str:
    """Return the canonical IPv4 AP rules plus IPv4/IPv6 default denial."""

    subnet = "10.42.0.0/24"
    broadcast = "255.255.255.255"
    interface = FACTORY_INTERFACE
    address = FACTORY_ADDRESS
    lines = _table_prefix(table_exists)
    lines.extend(
        (
            _rule("input", 'iifname "lo" accept', "ecobin-factory:loopback-input"),
            _rule("output", 'oifname "lo" accept', "ecobin-factory:loopback-output"),
            _rule("input", "ct state invalid drop", "ecobin-factory:invalid-input"),
            _rule("output", "ct state invalid drop", "ecobin-factory:invalid-output"),
            _rule(
                "input",
                f'iifname "{interface}" ip saddr 0.0.0.0 ip daddr {broadcast} '
                "udp sport 68 udp dport 67 accept",
                "ecobin-factory:dhcp-discover-input",
            ),
            _rule(
                "input",
                f'iifname "{interface}" ip saddr {subnet} ip daddr {address} '
                "udp sport 68 udp dport 67 accept",
                "ecobin-factory:dhcp-renew-input",
            ),
            _rule(
                "input",
                f'iifname "{interface}" ip saddr {subnet} ip daddr {broadcast} '
                "udp sport 68 udp dport 67 accept",
                "ecobin-factory:dhcp-rebind-input",
            ),
            _rule(
                "output",
                f'oifname "{interface}" ip saddr {address} ip daddr {broadcast} '
                "udp sport 67 udp dport 68 accept",
                "ecobin-factory:dhcp-broadcast-output",
            ),
            _rule(
                "output",
                f'oifname "{interface}" ip saddr {address} ip daddr {subnet} '
                "udp sport 67 udp dport 68 accept",
                "ecobin-factory:dhcp-unicast-output",
            ),
            _rule(
                "input",
                f'iifname "{interface}" ip saddr {subnet} ip daddr {address} '
                "udp dport 53 accept",
                "ecobin-factory:dns-udp-input",
            ),
            _rule(
                "output",
                f'oifname "{interface}" ip saddr {address} ip daddr {subnet} '
                "udp sport 53 ct state established accept",
                "ecobin-factory:dns-udp-output",
            ),
            _rule(
                "input",
                f'iifname "{interface}" ip saddr {subnet} ip daddr {address} '
                "tcp dport 53 accept",
                "ecobin-factory:dns-tcp-input",
            ),
            _rule(
                "output",
                f'oifname "{interface}" ip saddr {address} ip daddr {subnet} '
                "tcp sport 53 ct state established accept",
                "ecobin-factory:dns-tcp-output",
            ),
            _rule(
                "input",
                f'iifname "{interface}" ip saddr {subnet} ip daddr {address} '
                f"tcp dport {FACTORY_PORT} accept",
                "ecobin-factory:http-input",
            ),
            _rule(
                "output",
                f'oifname "{interface}" ip saddr {address} ip daddr {subnet} '
                f"tcp sport {FACTORY_PORT} ct state established accept",
                "ecobin-factory:http-output",
            ),
        )
    )
    return "\n".join(lines) + "\n"


Runner = Callable[[Sequence[str], bytes], subprocess.CompletedProcess[bytes]]


def _default_runner(
    command: Sequence[str], standard_input: bytes
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        input=standard_input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=5,
    )


def _invoke(
    arguments: Sequence[str], rules: str, runner: Runner
) -> subprocess.CompletedProcess[bytes]:
    return runner((NFT_PATH, *arguments), rules.encode("utf-8"))


def _json_document(result: subprocess.CompletedProcess[bytes], operation: str) -> dict[str, Any]:
    if result.returncode != 0:
        raise NftApplyError(f"nftables {operation} failed")
    try:
        document: Any = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NftApplyError(f"nftables {operation} returned invalid JSON") from exc
    if not isinstance(document, dict) or not isinstance(document.get("nftables"), list):
        raise NftApplyError(f"nftables {operation} returned an invalid document")
    return document


def _table_exists(runner: Runner) -> bool:
    result = _invoke(("-j", "list", "tables"), "", runner)
    document = _json_document(result, "table discovery")
    for entry in document["nftables"]:
        if not isinstance(entry, dict):
            raise NftApplyError("nftables table discovery returned an invalid entry")
        table = entry.get("table")
        if isinstance(table, dict) and table.get("family") == "inet" and table.get("name") == NFT_TABLE:
            return True
    return False


def _verify_profile(
    runner: Runner,
    expected_markers: frozenset[str],
    *,
    expected_policies: dict[str, str] | None = None,
) -> None:
    policies = expected_policies or {
        "input": "drop",
        "output": "drop",
        "forward": "drop",
    }
    result = _invoke(("-j", "list", "table", "inet", NFT_TABLE), "", runner)
    document = _json_document(result, "canonical table verification")

    tables: list[dict[str, Any]] = []
    chains: dict[str, dict[str, Any]] = {}
    markers: set[str] = set()
    for entry in document["nftables"]:
        if not isinstance(entry, dict):
            raise NftApplyError("canonical nftables table contains an invalid entry")
        if "metainfo" in entry:
            continue
        if set(entry) == {"table"} and isinstance(entry["table"], dict):
            tables.append(entry["table"])
            continue
        if set(entry) == {"chain"} and isinstance(entry["chain"], dict):
            chain = entry["chain"]
            name = chain.get("name")
            if not isinstance(name, str) or name in chains:
                raise NftApplyError("canonical nftables table has duplicate chains")
            chains[name] = chain
            continue
        if set(entry) == {"rule"} and isinstance(entry["rule"], dict):
            rule = entry["rule"]
            marker = rule.get("comment")
            if (
                rule.get("family") != "inet"
                or rule.get("table") != NFT_TABLE
                or rule.get("chain") not in {"input", "output", "forward"}
                or not isinstance(marker, str)
                or marker in markers
            ):
                raise NftApplyError("canonical nftables table has an invalid rule")
            markers.add(marker)
            continue
        raise NftApplyError("canonical nftables table contains an unexpected object")

    if len(tables) != 1 or tables[0].get("family") != "inet" or tables[0].get("name") != NFT_TABLE:
        raise NftApplyError("canonical nftables table identity does not match")
    if set(chains) != {"input", "output", "forward"}:
        raise NftApplyError("canonical nftables base-chain set does not match")
    for name, chain in chains.items():
        if (
            chain.get("family") != "inet"
            or chain.get("table") != NFT_TABLE
            or chain.get("type") != "filter"
            or chain.get("hook") != name
            or chain.get("prio") != FILTER_PRIORITY
            or chain.get("policy") != policies[name]
        ):
            raise NftApplyError("canonical nftables base-chain properties do not match")
    if markers != set(expected_markers):
        raise NftApplyError("canonical nftables rule set does not match")


def _apply_profile(
    renderer: Callable[..., str],
    expected_markers: frozenset[str],
    runner: Runner,
    *,
    expected_policies: dict[str, str] | None = None,
) -> str:
    rules = renderer(table_exists=_table_exists(runner))
    result = _invoke(("-f", "-"), rules, runner)
    if result.returncode != 0:
        raise NftApplyError("unable to establish the canonical network lock")
    _verify_profile(
        runner,
        expected_markers,
        expected_policies=expected_policies,
    )
    return rules


def _restore_emergency(runner: Runner) -> None:
    _apply_profile(render_emergency_lock, EMERGENCY_RULE_MARKERS, runner)


def apply_factory_egress_lock(*, runner: Runner | None = None) -> None:
    """Atomically load and prove the canonical offline factory rules."""

    execute = runner or _default_runner
    _apply_profile(render_emergency_lock, EMERGENCY_RULE_MARKERS, execute)

    desired = render_factory_egress_lock(table_exists=True)
    check = _invoke(("-c", "-f", "-"), desired, execute)
    if check.returncode != 0:
        raise NftApplyError("factory nftables rules failed validation")

    apply = _invoke(("-f", "-"), desired, execute)
    if apply.returncode != 0:
        try:
            _restore_emergency(execute)
        except NftApplyError as restore_error:
            raise NftApplyError(
                "factory nftables apply failed and emergency lock could not be proven"
            ) from restore_error
        raise NftApplyError("factory nftables apply failed; emergency lock remains active")

    try:
        _verify_profile(execute, FACTORY_RULE_MARKERS)
    except NftApplyError as verify_error:
        try:
            _restore_emergency(execute)
        except NftApplyError as restore_error:
            raise NftApplyError(
                "factory nftables verification failed and emergency lock could not be proven"
            ) from restore_error
        raise NftApplyError(
            "factory nftables verification failed; emergency lock remains active"
        ) from verify_error


def main() -> int:
    try:
        apply_factory_egress_lock()
    except (NftApplyError, OSError, subprocess.SubprocessError) as exc:
        # Never print nft stderr or rules; a stable error class is enough for
        # the first-boot status projection.
        print(f"factory egress lock unavailable: {type(exc).__name__}")
        return 1
    print("factory egress lock active and verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
