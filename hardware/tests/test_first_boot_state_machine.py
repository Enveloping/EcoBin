from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from first_boot.model import FactoryTestStatus, FirstBootFacts, FirstBootStage
from first_boot.orchestrator import FirstBootOrchestrator
from first_boot.state_machine import gate_allows, reconcile
from first_boot.state_store import FirstBootState, FirstBootStateStore
from first_boot.status_projection import PortalStatusProjector


def _previous(stage: FirstBootStage, generation: int = 1) -> FirstBootState:
    return FirstBootState(1, stage, generation, "NONE", "2026-08-22T00:00:00Z")


def _accepted(**overrides: object) -> FirstBootFacts:
    values: dict[str, object] = {
        "system_prepared": True,
        "factory_portal_ready": True,
        "factory_test_status": FactoryTestStatus.PASSED,
        "factory_report_valid": True,
        "cellular_profile_active": True,
        "uplink_ready": True,
        "time_trusted": True,
        "enrollment_complete": True,
    }
    values.update(overrides)
    return FirstBootFacts(**values)


@pytest.mark.parametrize(
    ("facts", "previous", "stage"),
    [
        (FirstBootFacts(), None, FirstBootStage.SYSTEM_PREPARED),
        (
            FirstBootFacts(system_prepared=True),
            None,
            FirstBootStage.FACTORY_PORTAL_READY,
        ),
        (
            FirstBootFacts(system_prepared=True, factory_portal_ready=True),
            None,
            FirstBootStage.FACTORY_TEST_REQUIRED,
        ),
        (
            FirstBootFacts(
                system_prepared=True,
                factory_portal_ready=True,
                factory_test_status=FactoryTestStatus.RUNNING,
            ),
            None,
            FirstBootStage.FACTORY_TEST_RUNNING,
        ),
        (_accepted(), None, FirstBootStage.FACTORY_TEST_PASSED),
        (
            _accepted(cellular_profile_active=False, uplink_ready=False),
            _previous(FirstBootStage.FACTORY_TEST_PASSED),
            FirstBootStage.UPLINK_REQUIRED,
        ),
        (
            _accepted(),
            _previous(FirstBootStage.UPLINK_REQUIRED),
            FirstBootStage.UPLINK_READY,
        ),
        (
            _accepted(enrollment_complete=False),
            _previous(FirstBootStage.UPLINK_READY),
            FirstBootStage.ENROLLMENT_REQUIRED,
        ),
        (
            _accepted(),
            _previous(FirstBootStage.ENROLLMENT_REQUIRED),
            FirstBootStage.ENROLLMENT_COMPLETE,
        ),
        (
            _accepted(sealed_exists=True, sealed_valid=True),
            None,
            FirstBootStage.SEALED,
        ),
        (
            _accepted(
                sealed_exists=True,
                sealed_valid=True,
                sealed_cleanup_complete=True,
            ),
            None,
            FirstBootStage.COMPLETE,
        ),
    ],
)
def test_stage_is_derived_from_current_facts(
    facts: FirstBootFacts,
    previous: FirstBootState | None,
    stage: FirstBootStage,
) -> None:
    assert reconcile(facts, previous).stage is stage


def test_missing_p7_report_always_stops_before_uplink() -> None:
    stale = _previous(FirstBootStage.ENROLLMENT_COMPLETE, generation=99)
    facts = _accepted(factory_report_valid=False)

    decision = reconcile(facts, stale)

    assert decision.stage is FirstBootStage.FACTORY_TEST_REQUIRED
    assert not gate_allows("factory-test-passed", facts)
    assert not gate_allows("enrollment", facts)
    assert not gate_allows("runtime", facts)


def test_recovery_lock_overrides_stale_passed_state() -> None:
    decision = reconcile(
        _accepted(
            factory_test_status=FactoryTestStatus.RECOVERY_REQUIRED,
            factory_recovery_required=True,
        ),
        _previous(FirstBootStage.ENROLLMENT_COMPLETE),
    )

    assert decision.stage is FirstBootStage.FACTORY_TEST_REQUIRED
    assert decision.error_code == "FACTORY_RECOVERY_REQUIRED"


def test_any_sealed_file_is_one_way_and_never_reopens_factory() -> None:
    facts = FirstBootFacts(sealed_exists=True, sealed_valid=False)

    decision = reconcile(facts, _previous(FirstBootStage.FACTORY_TEST_REQUIRED))

    assert decision.stage is FirstBootStage.SEALED
    assert decision.error_code == "SEALED_FACT_INVALID"
    assert not gate_allows("factory-test", facts)


def test_runtime_gate_requires_independent_handoff_fact() -> None:
    facts = _accepted(handoff_safe=False)
    assert gate_allows("handoff", facts)
    assert not gate_allows("runtime", facts)
    assert gate_allows("runtime", _accepted(handoff_safe=True))


@pytest.mark.parametrize(
    "requirement",
    ["factory-test", "factory-test-passed", "enrollment", "handoff", "runtime"],
)
def test_every_downstream_gate_requires_current_boot_system_preparation(
    requirement: str,
) -> None:
    facts = _accepted(
        system_prepared=False,
        handoff_safe=True,
        sealed_exists=True,
        sealed_valid=True,
        sealed_cleanup_complete=True,
    )

    assert not gate_allows(requirement, facts)


def test_sealed_state_remains_one_way_when_current_boot_gpio_fact_is_missing() -> None:
    facts = _accepted(
        system_prepared=False,
        handoff_safe=True,
        sealed_exists=True,
        sealed_valid=True,
        sealed_cleanup_complete=True,
    )

    assert reconcile(facts).stage is FirstBootStage.COMPLETE
    assert not gate_allows("factory-test", facts)
    assert not gate_allows("runtime", facts)


def test_state_is_mode_0600_and_survives_replace_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "first-boot" / "state.json"
    store = FirstBootStateStore(path, clock=lambda: "2026-08-22T00:00:00Z")
    first = store.save(
        FirstBootStage.FACTORY_TEST_REQUIRED,
        previous=None,
        last_error_code="NONE",
    )
    original = path.read_bytes()
    real_replace = os.replace

    def interrupted(source: object, target: object) -> None:
        raise OSError("simulated power loss before rename")

    monkeypatch.setattr("first_boot.atomic_json.os.replace", interrupted)
    with pytest.raises(Exception, match="atomic JSON write failed"):
        store.save(
            FirstBootStage.FACTORY_TEST_RUNNING,
            previous=first,
            last_error_code="NONE",
        )
    assert path.read_bytes() == original
    assert store.load() == first
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o710
    monkeypatch.setattr("first_boot.atomic_json.os.replace", real_replace)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission gate")
def test_state_rejects_permissive_existing_index(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = FirstBootStateStore(path)
    store.save(
        FirstBootStage.FACTORY_TEST_REQUIRED,
        previous=None,
        last_error_code="NONE",
    )
    path.chmod(0o644)

    with pytest.raises(ValueError, match="permissions"):
        store.load()


class _FixedFacts:
    def __init__(self, facts: FirstBootFacts) -> None:
        self.value = facts

    def collect(self) -> FirstBootFacts:
        return self.value


class _NoActions:
    def apply(self, stage: FirstBootStage, facts: FirstBootFacts) -> str:
        return "NONE"


def test_force_kill_restart_rechecks_facts_not_old_stage(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    public_path = tmp_path / "status.json"
    owner_calls: list[Path] = []

    def owner(path: Path) -> None:
        owner_calls.append(path)

    first = FirstBootOrchestrator(
        _FixedFacts(_accepted()),
        FirstBootStateStore(state_path),
        PortalStatusProjector(public_path, owner=owner),
        _NoActions(),
    ).run_once()
    assert first.stage is FirstBootStage.FACTORY_TEST_PASSED

    # A new process sees the old advanced index but a missing report.  It must
    # move back to the safe gate rather than trusting state.json.
    second = FirstBootOrchestrator(
        _FixedFacts(_accepted(factory_report_valid=False)),
        FirstBootStateStore(state_path),
        PortalStatusProjector(public_path, owner=owner),
        _NoActions(),
    ).run_once()

    assert second.stage is FirstBootStage.FACTORY_TEST_REQUIRED
    assert json.loads(public_path.read_text(encoding="utf-8"))["stage"] == second.stage
    assert owner_calls
