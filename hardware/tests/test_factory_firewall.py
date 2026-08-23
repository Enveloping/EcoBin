from __future__ import annotations

import json
import subprocess

import pytest

from factory.firewall import (
    EMERGENCY_RULE_MARKERS,
    FACTORY_RULE_MARKERS,
    FILTER_PRIORITY,
    NftApplyError,
    apply_factory_egress_lock,
    render_emergency_lock,
    render_factory_egress_lock,
)


def _completed(
    command: tuple[str, ...], returncode: int, payload: dict[str, object] | None = None
) -> subprocess.CompletedProcess[bytes]:
    stdout = b"" if payload is None else json.dumps(payload).encode("utf-8")
    return subprocess.CompletedProcess(command, returncode, stdout, b"")


def _table_document(
    markers: frozenset[str], *, priority: int = FILTER_PRIORITY
) -> dict[str, object]:
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
                "prio": priority,
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


class StatefulNftRunner:
    def __init__(
        self,
        *,
        initial_table: bool = False,
        fail_emergency_apply: bool = False,
        fail_check: bool = False,
        fail_desired_apply: bool = False,
        tamper_desired_verification: bool = False,
    ) -> None:
        self.profile: str | None = "malformed" if initial_table else None
        self.fail_emergency_apply = fail_emergency_apply
        self.fail_check = fail_check
        self.fail_desired_apply = fail_desired_apply
        self.tamper_desired_verification = tamper_desired_verification
        self.calls: list[tuple[tuple[str, ...], bytes]] = []

    def __call__(
        self, command: tuple[str, ...], standard_input: bytes
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((command, standard_input))
        arguments = command[1:]
        if arguments == ("-j", "list", "tables"):
            entries: list[dict[str, object]] = [{"metainfo": {}}]
            if self.profile is not None:
                entries.append(
                    {"table": {"family": "inet", "name": "ecobin_factory"}}
                )
            return _completed(command, 0, {"nftables": entries})
        if arguments == ("-j", "list", "table", "inet", "ecobin_factory"):
            if self.profile == "factory":
                markers = FACTORY_RULE_MARKERS
                if self.tamper_desired_verification:
                    markers = frozenset(set(markers) - {"ecobin-factory:http-output"})
                    self.tamper_desired_verification = False
                return _completed(command, 0, _table_document(markers))
            if self.profile == "emergency":
                return _completed(command, 0, _table_document(EMERGENCY_RULE_MARKERS))
            return _completed(command, 1)
        if arguments == ("-c", "-f", "-"):
            return _completed(command, 1 if self.fail_check else 0)
        if arguments == ("-f", "-"):
            desired = b"ecobin-factory:http-output" in standard_input
            if desired and self.fail_desired_apply:
                return _completed(command, 1)
            if not desired and self.profile is None and self.fail_emergency_apply:
                return _completed(command, 1)
            self.profile = "factory" if desired else "emergency"
            return _completed(command, 0)
        raise AssertionError(f"unexpected nft invocation: {command!r}")


def test_factory_rules_default_drop_and_expose_only_local_ap_services() -> None:
    rules = render_factory_egress_lock()

    assert rules.count("policy drop") == 3
    assert "priority -150" in rules
    assert "priority -300" not in rules
    assert rules.startswith("delete table inet ecobin_factory\nadd table")
    assert 'iifname "wlan0"' in rules
    assert 'oifname "wlan0"' in rules
    assert "ip daddr 10.42.0.1 udp dport 53 accept" in rules
    assert "ip daddr 10.42.0.1 tcp dport 53 accept" in rules
    assert "ip daddr 10.42.0.1 tcp dport 80 accept" in rules
    assert "ip saddr 0.0.0.0 ip daddr 255.255.255.255" in rules
    assert "ip saddr 10.42.0.1 ip daddr 255.255.255.255" in rules
    assert "ct state established" in rules
    assert 'comment "ecobin-factory:' in rules
    assert "masquerade" not in rules.lower()
    assert "policy accept" not in rules.lower()
    assert "eth0" not in rules
    assert "wwan" not in rules.lower()
    assert "usb0" not in rules.lower()
    assert " dport 22 " not in rules
    assert " dport 443 " not in rules
    assert " dport 1883 " not in rules


def test_emergency_rules_allow_only_loopback() -> None:
    emergency = render_emergency_lock()

    assert emergency.count("policy drop") == 3
    assert "priority -150" in emergency
    assert emergency.startswith("delete table inet ecobin_factory\nadd table")
    assert emergency.count(" accept ") == 2
    assert 'iifname "lo" accept' in emergency
    assert 'oifname "lo" accept' in emergency
    assert "wlan0" not in emergency


def test_absent_table_uses_add_while_existing_table_is_deleted_and_rebuilt() -> None:
    absent = render_emergency_lock(table_exists=False)
    existing = render_emergency_lock(table_exists=True)

    assert absent.startswith("add table inet ecobin_factory")
    assert "delete table" not in absent
    assert existing.startswith("delete table inet ecobin_factory")


def test_apply_proves_emergency_then_checks_applies_and_proves_canonical_rules() -> None:
    runner = StatefulNftRunner()

    apply_factory_egress_lock(runner=runner)

    assert runner.profile == "factory"
    commands = [call[0][1:] for call in runner.calls]
    assert commands == [
        ("-j", "list", "tables"),
        ("-f", "-"),
        ("-j", "list", "table", "inet", "ecobin_factory"),
        ("-c", "-f", "-"),
        ("-f", "-"),
        ("-j", "list", "table", "inet", "ecobin_factory"),
    ]
    assert runner.calls[1][1].startswith(b"add table inet ecobin_factory")


def test_existing_malformed_table_is_deleted_in_same_emergency_transaction() -> None:
    runner = StatefulNftRunner(initial_table=True)

    apply_factory_egress_lock(runner=runner)

    assert runner.calls[1][1].startswith(
        b"delete table inet ecobin_factory\nadd table inet ecobin_factory"
    )


def test_failed_validation_leaves_proven_emergency_lock() -> None:
    runner = StatefulNftRunner(fail_check=True)

    with pytest.raises(NftApplyError, match="failed validation"):
        apply_factory_egress_lock(runner=runner)

    assert runner.profile == "emergency"


def test_failed_final_apply_rebuilds_and_reproves_emergency_lock() -> None:
    runner = StatefulNftRunner(fail_desired_apply=True)

    with pytest.raises(NftApplyError, match="emergency lock remains active"):
        apply_factory_egress_lock(runner=runner)

    assert runner.profile == "emergency"
    assert sum(call[0][1:] == ("-j", "list", "tables") for call in runner.calls) == 2


def test_failed_post_apply_canonical_verification_restores_emergency() -> None:
    runner = StatefulNftRunner(tamper_desired_verification=True)

    with pytest.raises(NftApplyError, match="verification failed"):
        apply_factory_egress_lock(runner=runner)

    assert runner.profile == "emergency"


def test_no_desired_attempt_occurs_when_emergency_lock_cannot_load() -> None:
    runner = StatefulNftRunner(fail_emergency_apply=True)

    with pytest.raises(NftApplyError, match="canonical network lock"):
        apply_factory_egress_lock(runner=runner)

    assert len(runner.calls) == 2
    assert all(b"ecobin-factory:http-output" not in payload for _, payload in runner.calls)
