from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from factory.firewall import EMERGENCY_RULE_MARKERS, FILTER_PRIORITY
from first_boot.cellular_firewall import (
    FACTORY_UPLINK_RULE_MARKERS,
    PRODUCTION_UPLINK_RULE_MARKERS,
    apply_emergency_uplink_lock,
    apply_factory_uplink_gate,
    apply_production_uplink_gate,
    apply_seal_aware_uplink_gate,
    render_factory_uplink_gate,
    render_production_uplink_gate,
)


def _completed(
    command: tuple[str, ...],
    returncode: int,
    payload: dict[str, object] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    output = b"" if payload is None else json.dumps(payload).encode("utf-8")
    return subprocess.CompletedProcess(command, returncode, output, b"")


def _table_document(markers: frozenset[str]) -> dict[str, object]:
    entries: list[dict[str, object]] = [
        {"metainfo": {"json_schema_version": 1}},
        {"table": {"family": "inet", "name": "ecobin_factory"}},
    ]
    entries.extend(
        {
            "chain": {
                "family": "inet",
                "table": "ecobin_factory",
                "name": name,
                "type": "filter",
                "hook": name,
                "prio": FILTER_PRIORITY,
                "policy": "drop",
            }
        }
        for name in ("input", "output", "forward")
    )
    entries.extend(
        {
            "rule": {
                "family": "inet",
                "table": "ecobin_factory",
                "chain": "input" if marker.endswith("input") else "output",
                "expr": [],
                "comment": marker,
            }
        }
        for marker in sorted(markers)
    )
    return {"nftables": entries}


class StatefulCellularNftRunner:
    def __init__(
        self,
        *,
        initial_table: bool = True,
        fail_check: bool = False,
        fail_candidate_apply: bool = False,
        tamper_candidate_verification: bool = False,
    ) -> None:
        self.profile: str | None = "malformed" if initial_table else None
        self.fail_check = fail_check
        self.fail_candidate_apply = fail_candidate_apply
        self.tamper_candidate_verification = tamper_candidate_verification
        self.calls: list[tuple[tuple[str, ...], bytes]] = []

    def __call__(
        self, command: tuple[str, ...], payload: bytes
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((command, payload))
        arguments = command[1:]
        if arguments == ("-j", "list", "tables"):
            entries: list[dict[str, object]] = [{"metainfo": {}}]
            if self.profile is not None:
                entries.append(
                    {"table": {"family": "inet", "name": "ecobin_factory"}}
                )
            return _completed(command, 0, {"nftables": entries})
        if arguments == ("-j", "list", "table", "inet", "ecobin_factory"):
            marker_sets = {
                "factory": FACTORY_UPLINK_RULE_MARKERS,
                "production": PRODUCTION_UPLINK_RULE_MARKERS,
                "emergency": EMERGENCY_RULE_MARKERS,
            }
            markers = marker_sets.get(self.profile)
            if markers is None:
                return _completed(command, 1)
            if (
                self.profile in {"factory", "production"}
                and self.tamper_candidate_verification
            ):
                markers = frozenset(
                    set(markers) - {"ecobin-cellular:established-input"}
                )
                self.tamper_candidate_verification = False
            return _completed(command, 0, _table_document(markers))
        if arguments == ("-c", "-f", "-"):
            return _completed(command, 1 if self.fail_check else 0)
        if arguments == ("-f", "-"):
            is_factory = b"ecobin-cellular:factory-http-input" in payload
            is_cellular = b"ecobin-cellular:tcp-output" in payload
            is_candidate = is_factory or is_cellular
            if is_candidate and self.fail_candidate_apply:
                return _completed(command, 1)
            self.profile = (
                "factory"
                if is_factory
                else "production"
                if is_cellular
                else "emergency"
            )
            return _completed(command, 0)
        raise AssertionError(f"unexpected nft invocation: {command!r}")


def test_factory_uplink_keeps_ap_local_and_binds_all_wan_to_rndis() -> None:
    rules = render_factory_uplink_gate("enxcell0")

    assert rules.startswith(
        "delete table inet ecobin_factory\nadd table inet ecobin_factory"
    )
    assert rules.count("policy drop") == 3
    assert "priority -150" in rules
    assert 'oifname "enxcell0"' in rules
    assert 'iifname "enxcell0" ct state established,related accept' in rules
    assert (
        'iifname "wlan0" ip saddr 10.42.0.0/24 ip daddr 10.42.0.1 '
        "tcp dport 80 accept"
    ) in rules
    assert 'iifname "wlan0" tcp dport 22' not in rules
    assert 'oifname "eth0"' not in rules
    assert "masquerade" not in rules.lower()
    assert "policy accept" not in rules.lower()


def test_production_uplink_removes_every_factory_ap_allow_rule() -> None:
    rules = render_production_uplink_gate("enxcell0")

    assert rules.count("policy drop") == 3
    assert 'oifname "enxcell0"' in rules
    assert "wlan0" not in rules
    assert "factory-http" not in rules
    assert " dport 22 " not in rules
    assert "policy accept" not in rules.lower()


@pytest.mark.parametrize(
    "interface", ["wlan0", "bad name", "eth0;delete table inet filter"]
)
def test_uplink_gate_rejects_untrusted_or_ap_interface(interface: str) -> None:
    with pytest.raises(ValueError, match="CELLULAR_INTERFACE_INVALID"):
        render_factory_uplink_gate(interface)
    with pytest.raises(ValueError, match="CELLULAR_INTERFACE_INVALID"):
        render_production_uplink_gate(interface)


@pytest.mark.parametrize(
    ("apply", "expected_profile"),
    (
        (apply_factory_uplink_gate, "factory"),
        (apply_production_uplink_gate, "production"),
    ),
)
def test_existing_table_is_atomically_replaced_and_verified(
    apply, expected_profile: str, tmp_path: Path
) -> None:
    runner = StatefulCellularNftRunner()

    assert apply(
        "enxcell0", runner=runner, lock_path=tmp_path / "firewall.lock"
    )

    assert runner.profile == expected_profile
    assert [call[0][1:] for call in runner.calls] == [
        ("-j", "list", "tables"),
        ("-c", "-f", "-"),
        ("-f", "-"),
        ("-j", "list", "table", "inet", "ecobin_factory"),
    ]
    assert runner.calls[2][1].startswith(
        b"delete table inet ecobin_factory\nadd table inet ecobin_factory"
    )


@pytest.mark.parametrize(
    "options",
    (
        {"fail_check": True},
        {"fail_candidate_apply": True},
        {"tamper_candidate_verification": True},
    ),
)
def test_every_candidate_failure_restores_and_reproves_emergency(
    options: dict[str, bool],
    tmp_path: Path,
) -> None:
    runner = StatefulCellularNftRunner(**options)

    assert not apply_factory_uplink_gate(
        "enxcell0", runner=runner, lock_path=tmp_path / "firewall.lock"
    )

    assert runner.profile == "emergency"
    assert any(
        call[0][1:] == ("-j", "list", "table", "inet", "ecobin_factory")
        for call in runner.calls
    )


def test_missing_early_table_is_a_failure_and_ends_in_emergency(
    tmp_path: Path,
) -> None:
    runner = StatefulCellularNftRunner(initial_table=False)

    assert not apply_production_uplink_gate(
        "enxcell0", runner=runner, lock_path=tmp_path / "firewall.lock"
    )

    assert runner.profile == "emergency"
    assert not any(
        b"ecobin-cellular:tcp-output" in payload for _command, payload in runner.calls
    )


def test_public_emergency_operation_builds_and_proves_the_lock(
    tmp_path: Path,
) -> None:
    runner = StatefulCellularNftRunner(initial_table=False)

    assert apply_emergency_uplink_lock(
        runner=runner, lock_path=tmp_path / "firewall.lock"
    )

    assert runner.profile == "emergency"


def test_seal_aware_loop_never_reopens_factory_rules_after_valid_seal(
    tmp_path: Path,
) -> None:
    runner = StatefulCellularNftRunner()
    fact = [SimpleNamespace(exists=False, valid=False)]
    lock = tmp_path / "firewall.lock"

    first = apply_seal_aware_uplink_gate(
        "enxcell0", lambda: fact[0], runner=runner, lock_path=lock
    )
    fact[0] = SimpleNamespace(exists=True, valid=True)
    seal_transition_call = len(runner.calls)
    second = apply_seal_aware_uplink_gate(
        "enxcell0", lambda: fact[0], runner=runner, lock_path=lock
    )
    third = apply_seal_aware_uplink_gate(
        "enxcell0", lambda: fact[0], runner=runner, lock_path=lock
    )

    assert (first, second, third) == ("FACTORY", "PRODUCTION", "PRODUCTION")
    assert runner.profile == "production"
    assert all(
        b"ecobin-cellular:factory-http-input" not in payload
        for _command, payload in runner.calls[seal_transition_call:]
    )


def test_seal_aware_invalid_marker_restores_emergency_without_candidate(
    tmp_path: Path,
) -> None:
    runner = StatefulCellularNftRunner()
    start = len(runner.calls)

    result = apply_seal_aware_uplink_gate(
        "enxcell0",
        lambda: SimpleNamespace(exists=True, valid=False),
        runner=runner,
        lock_path=tmp_path / "firewall.lock",
    )

    assert result == "SEALED_FACT_INVALID"
    assert runner.profile == "emergency"
    assert all(
        b"ecobin-cellular:tcp-output" not in payload
        for _command, payload in runner.calls[start:]
    )
