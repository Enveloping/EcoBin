from __future__ import annotations

from pathlib import Path

from first_boot.command import CommandResult
from first_boot.model import FactoryTestStatus, FirstBootFacts, FirstBootStage
from first_boot.orchestrator import (
    FirstBootOrchestrator,
    SystemdStageActions,
)
from first_boot.state_store import FirstBootStateStore
from first_boot.status_projection import PortalStatusProjector


class RecordingRunner:
    def __init__(
        self,
        *,
        return_code: int = 0,
        active_units: set[str] | None = None,
    ) -> None:
        self.return_code = return_code
        self.active_units = active_units or set()
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
        self.calls.append(tuple(argv))
        if argv[1:3] == ("is-active", "--quiet"):
            return CommandResult(0 if argv[-1] in self.active_units else 3, "")
        return CommandResult(self.return_code, "")


def _start_calls(runner: RecordingRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if call[1] == "start"]


def _passed(**overrides: object) -> FirstBootFacts:
    values: dict[str, object] = {
        "system_prepared": True,
        "factory_portal_ready": True,
        "factory_test_status": FactoryTestStatus.PASSED,
        "factory_report_valid": True,
        "factory_recovery_required": False,
        "cellular_profile_active": True,
        "uplink_ready": True,
        "time_trusted": True,
        "enrollment_complete": True,
        "handoff_safe": True,
    }
    values.update(overrides)
    return FirstBootFacts(**values)


def test_unsealed_factory_to_runtime_sequence_keeps_ap_target_running() -> None:
    runner = RecordingRunner()
    actions = SystemdStageActions(runner)

    assert actions.apply(
        FirstBootStage.FACTORY_PORTAL_READY,
        FirstBootFacts(system_prepared=True),
    ) == "NONE"
    assert actions.apply(
        FirstBootStage.FACTORY_TEST_REQUIRED,
        FirstBootFacts(system_prepared=True, factory_portal_ready=True),
    ) == "NONE"
    assert actions.apply(
        FirstBootStage.UPLINK_REQUIRED,
        _passed(cellular_profile_active=False, uplink_ready=False),
    ) == "NONE"
    assert actions.apply(
        FirstBootStage.ENROLLMENT_REQUIRED,
        _passed(enrollment_complete=False, handoff_safe=False),
    ) == "NONE"
    assert actions.apply(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed(handoff_safe=False),
    ) == "NONE"
    assert actions.apply(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed(handoff_safe=True),
    ) == "NONE"

    assert [call[-1] for call in _start_calls(runner)] == [
        "ecobin-factory.target",
        "ecobin-factory-test.service",
        "ecobin-cellular-uplink.service",
        "ecobin-enrollment.service",
        "ecobin-factory-handoff.service",
        "ecobin-runtime.target",
    ]
    assert all("stop" not in call for call in runner.calls)


def test_repeated_factory_stage_does_not_resubmit_active_service_dependencies() -> None:
    runner = RecordingRunner(active_units={"ecobin-factory-test.service"})
    actions = SystemdStageActions(runner)
    facts = FirstBootFacts(
        system_prepared=True,
        factory_portal_ready=True,
    )

    assert actions.apply(FirstBootStage.FACTORY_TEST_REQUIRED, facts) == "NONE"
    assert actions.apply(FirstBootStage.FACTORY_TEST_REQUIRED, facts) == "NONE"

    assert _start_calls(runner) == []
    assert [call[-1] for call in runner.calls] == [
        "ecobin-factory-test.service",
        "ecobin-factory-test.service",
    ]


def test_sealed_cold_boot_starts_only_cellular_then_runtime_never_factory() -> None:
    runner = RecordingRunner()
    actions = SystemdStageActions(runner)

    assert actions.apply(
        FirstBootStage.COMPLETE,
        _passed(
            sealed_exists=True,
            sealed_valid=True,
            sealed_cleanup_complete=True,
            cellular_profile_active=False,
            uplink_ready=False,
        ),
    ) == "NONE"
    assert actions.apply(
        FirstBootStage.COMPLETE,
        _passed(
            sealed_exists=True,
            sealed_valid=True,
            sealed_cleanup_complete=True,
        ),
    ) == "NONE"

    assert [call[-1] for call in _start_calls(runner)] == [
        "ecobin-cellular-uplink.service",
        "ecobin-runtime.target",
    ]
    assert not any("factory.target" in " ".join(call) for call in runner.calls)
    assert not any("factory-test.service" in " ".join(call) for call in runner.calls)


class FixedFacts:
    def __init__(self, facts: FirstBootFacts) -> None:
        self.facts = facts

    def collect(self) -> FirstBootFacts:
        return self.facts


class RecordingActions:
    def __init__(self) -> None:
        self.calls: list[FirstBootStage] = []

    def apply(self, stage: FirstBootStage, facts: FirstBootFacts) -> str:
        self.calls.append(stage)
        return "NONE"


class FailedSealCleanup:
    def reconcile_cleanup(self) -> str:
        return "PRODUCTION_FIREWALL_FAILED"


class EventProjector:
    def __init__(self, events: list[str], name: str) -> None:
        self.events = events
        self.name = name

    def publish(self, *args, **kwargs) -> None:
        self.events.append(self.name)


class EventActions:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def apply(self, stage: FirstBootStage, facts: FirstBootFacts) -> str:
        self.events.append(f"action:{stage.value}")
        return "NONE"


def test_ap_allow_projection_is_committed_before_factory_target_action(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    FirstBootOrchestrator(
        FixedFacts(FirstBootFacts(system_prepared=True)),
        FirstBootStateStore(tmp_path / "ordered-state.json"),
        EventProjector(events, "portal"),  # type: ignore[arg-type]
        EventActions(events),
        ap_projector=EventProjector(events, "ap"),  # type: ignore[arg-type]
    ).run_once()

    assert events == [
        "ap",
        "portal",
        "action:FACTORY_PORTAL_READY",
    ]


def test_failed_seal_cleanup_is_projected_and_blocks_all_stage_actions(
    tmp_path: Path,
) -> None:
    actions = RecordingActions()
    state = FirstBootOrchestrator(
        FixedFacts(
            _passed(
                sealed_exists=True,
                sealed_valid=True,
                sealed_cleanup_complete=False,
            )
        ),
        FirstBootStateStore(tmp_path / "state.json"),
        PortalStatusProjector(
            tmp_path / "status.json", owner=lambda _path: None
        ),
        actions,
        FailedSealCleanup(),  # type: ignore[arg-type]
    ).run_once()

    assert state.stage is FirstBootStage.SEALED
    assert state.last_error_code == "PRODUCTION_FIREWALL_FAILED"
    assert actions.calls == []


def test_invalid_seal_never_starts_cellular_or_runtime() -> None:
    runner = RecordingRunner()
    actions = SystemdStageActions(runner)

    result = actions.apply(
        FirstBootStage.SEALED,
        _passed(sealed_exists=True, sealed_valid=False),
    )

    assert result == "SEALED_FACT_INVALID"
    assert runner.calls == []
