from __future__ import annotations

import argparse
import signal
import threading
from typing import Protocol, Sequence

from factory_seal.controller import FactorySealController
from factory_seal.portal_server import FactorySealPortalServer

from .command import CommandRunner
from .facts import FactsProvider, SystemFactsProvider
from .factory_flow import FactoryFlowProjector
from .model import FirstBootFacts, FirstBootStage
from .state_machine import gate_allows, reconcile
from .state_store import FirstBootState, FirstBootStateStore
from .status_projection import (
    AccessPointAuthorizationProjector,
    PortalStatusProjector,
)


class StageActions(Protocol):
    def apply(self, stage: FirstBootStage, facts: FirstBootFacts) -> str: ...


class SystemdStageActions:
    _RUNTIME_TARGET = "ecobin-runtime.target"
    _RUNTIME_MEMBERS = (
        "ecobin-communication.service",
        "ecobin-updater.service",
        "ecobin-business-activation-helper.socket",
        "ecobin-mcu-flash-helper.socket",
        "ecobin-device-management-preflight.service",
        "ecobin-hardware.service",
        "ecobin-remote-support.service",
    )

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or CommandRunner()

    def apply(self, stage: FirstBootStage, facts: FirstBootFacts) -> str:
        unit: str | None = None
        if stage is FirstBootStage.FACTORY_PORTAL_READY and not facts.sealed_exists:
            unit = "ecobin-factory.target"
        elif stage in {
            FirstBootStage.FACTORY_TEST_REQUIRED,
            FirstBootStage.FACTORY_TEST_RUNNING,
        } and not facts.sealed_exists:
            if not gate_allows("factory-test", facts):
                return "FACTORY_TEST_GATE_CLOSED"
            unit = "ecobin-factory-test.service"
        elif stage is FirstBootStage.UPLINK_REQUIRED:
            if not gate_allows("factory-test-passed", facts):
                return "FACTORY_TEST_GATE_CLOSED"
            unit = "ecobin-cellular-uplink.service"
        elif stage is FirstBootStage.ENROLLMENT_REQUIRED:
            if not gate_allows("enrollment", facts):
                return "ENROLLMENT_GATE_CLOSED"
            unit = "ecobin-enrollment.service"
        elif stage is FirstBootStage.ENROLLMENT_COMPLETE:
            if not facts.handoff_safe:
                if not gate_allows("handoff", facts):
                    return "HANDOFF_GATE_CLOSED"
                unit = "ecobin-factory-handoff.service"
            elif gate_allows("runtime", facts):
                unit = self._RUNTIME_TARGET
        elif stage in {FirstBootStage.SEALED, FirstBootStage.COMPLETE}:
            if not facts.sealed_valid:
                return "SEALED_FACT_INVALID"
            if not facts.uplink_ready or not facts.cellular_profile_active:
                if not gate_allows("factory-test-passed", facts):
                    return "FACTORY_TEST_GATE_CLOSED"
                unit = "ecobin-cellular-uplink.service"
            elif not facts.handoff_safe:
                if not gate_allows("handoff", facts):
                    return "HANDOFF_GATE_CLOSED"
                unit = "ecobin-factory-handoff.service"
            elif gate_allows("runtime", facts):
                unit = self._RUNTIME_TARGET
        if unit is None:
            return "NONE"
        if unit == self._RUNTIME_TARGET:
            return self._ensure_runtime_members()
        return self._start_if_inactive(unit)

    def _ensure_runtime_members(self) -> str:
        if not self._is_active(self._RUNTIME_TARGET):
            return self._start(self._RUNTIME_TARGET)

        failed = False
        for unit in self._RUNTIME_MEMBERS:
            if self._is_active(unit):
                continue
            if self._start(unit) != "NONE":
                failed = True
        return "STAGE_SERVICE_FAILED" if failed else "NONE"

    def _start_if_inactive(self, unit: str) -> str:
        if self._is_active(unit):
            # Re-submitting an already-active unit also starts inactive
            # Requires= dependencies in a new systemd transaction.  Most
            # stage services are boot-only prerequisites, so avoid replaying
            # those transactions on every reconciliation pass.
            return "NONE"
        return self._start(unit)

    def _is_active(self, unit: str) -> bool:
        active = self._runner.run(
            ("/usr/bin/systemctl", "is-active", "--quiet", unit),
            timeout_seconds=5,
        )
        return active.return_code == 0

    def _start(self, unit: str) -> str:
        result = self._runner.run(
            ("/usr/bin/systemctl", "start", unit),
            timeout_seconds=30,
        )
        return "NONE" if result.return_code == 0 else "STAGE_SERVICE_FAILED"


class FirstBootOrchestrator:
    def __init__(
        self,
        facts: FactsProvider,
        state: FirstBootStateStore,
        projector: PortalStatusProjector,
        actions: StageActions,
        seal_controller: FactorySealController | None = None,
        ap_projector: AccessPointAuthorizationProjector | None = None,
        flow_projector: FactoryFlowProjector | None = None,
    ) -> None:
        self._facts = facts
        self._state = state
        self._projector = projector
        self._actions = actions
        self._seal_controller = seal_controller
        self._ap_projector = ap_projector
        self._flow_projector = flow_projector

    def run_once(self) -> FirstBootState:
        cleanup_result = "NO_SEAL"
        if self._seal_controller is not None:
            try:
                cleanup_result = self._seal_controller.reconcile_cleanup()
            except Exception:
                # A seal path is one-way.  Cleanup uncertainty must be visible
                # and must suppress every action that could reopen networking
                # or production while the controller is in an unknown state.
                cleanup_result = "SEALED_CLEANUP_FAILED"
        observations = self._facts.collect()
        response_hold = False
        if self._seal_controller is not None:
            try:
                # Confirmation can commit on the portal thread after the
                # cleanup result above was sampled.  The current boot-scoped
                # hold is the authoritative fact for both AP projection and
                # action suppression in this cycle.
                response_hold = self._seal_controller.response_hold_active()
            except Exception:
                if cleanup_result in {"NO_SEAL", "SEALED"}:
                    cleanup_result = "SEALED_CLEANUP_FAILED"
        effective_cleanup_result = cleanup_result
        if response_hold and cleanup_result in {"NO_SEAL", "SEALED"}:
            effective_cleanup_result = "SEALED_RESPONSE_PENDING"
        if self._ap_projector is not None:
            self._ap_projector.publish(
                observations,
                allow_sealed_response=response_hold,
            )
        load_error = "NONE"
        try:
            previous = self._state.load()
        except ValueError:
            previous = None
            load_error = "STATE_INDEX_INVALID"
        decision = reconcile(observations, previous)
        cleanup_blocks_actions = effective_cleanup_result not in {
            "NO_SEAL",
            "SEALED",
        }
        error_code = (
            effective_cleanup_result
            if cleanup_blocks_actions
            else decision.error_code
            if decision.error_code != "NONE"
            else load_error
        )
        saved = self._state.save(
            decision.stage,
            previous=previous,
            last_error_code=error_code,
        )
        self._projector.publish(decision.stage, observations, error_code=error_code)
        self._publish_flow(decision.stage, observations, error_code)
        action_error = (
            "NONE"
            if cleanup_blocks_actions
            else self._actions.apply(decision.stage, observations)
        )
        if action_error != "NONE":
            saved = self._state.save(
                decision.stage,
                previous=saved,
                last_error_code=action_error,
            )
            self._projector.publish(
                decision.stage,
                observations,
                error_code=action_error,
            )
            self._publish_flow(decision.stage, observations, action_error)
        return saved

    def _publish_flow(
        self,
        stage: FirstBootStage,
        facts: FirstBootFacts,
        error_code: str,
    ) -> None:
        if self._flow_projector is None:
            return
        seal: dict[str, object] = {
            "authorized": False,
            "confirmAllowed": False,
            "statusCode": "FACTORY_SEAL_NOT_AVAILABLE",
            "acceptanceGeneration": None,
            "authorizationBindingSha256": None,
        }
        if self._seal_controller is not None:
            try:
                seal = self._seal_controller.status()
            except Exception:
                pass
        try:
            self._flow_projector.publish(
                stage,
                facts,
                error_code=error_code,
                seal=seal,
            )
        except Exception:
            # A diagnostic projection can become unavailable, but it must not
            # alter the authoritative first-boot or one-way seal state machine.
            return


def _reconciliation_interval_seconds(
    configured_seconds: float,
    seal_controller: FactorySealController,
) -> float:
    try:
        status_code = seal_controller.status().get("statusCode")
    except Exception:
        return min(configured_seconds, 0.25)
    if not isinstance(status_code, str) or status_code in {
        "SEALED_RESPONSE_PENDING",
        "SEALED_CLEANUP_PENDING",
    }:
        return min(configured_seconds, 0.25)
    return configured_seconds


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EcoBin fact-driven first boot")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=3.0)
    parser.add_argument("--portal-group", default="ecobin-factory-web")
    args = parser.parse_args(argv)
    if not 1 <= args.interval_seconds <= 60:
        parser.error("--interval-seconds must be between 1 and 60")
    import grp

    group_id = grp.getgrnam(args.portal_group).gr_gid
    wake_event = threading.Event()
    seal_controller = FactorySealController(wake_event=wake_event)
    seal_server = FactorySealPortalServer(
        seal_controller,
        group_id=group_id,
    )
    seal_server.start()
    orchestrator = FirstBootOrchestrator(
        SystemFactsProvider(),
        FirstBootStateStore(),
        PortalStatusProjector(group_id=group_id),
        SystemdStageActions(),
        seal_controller,
        AccessPointAuthorizationProjector(group_id=group_id),
        FactoryFlowProjector(group_id=group_id),
    )
    if args.once:
        try:
            orchestrator.run_once()
            return 0
        finally:
            seal_server.close()

    stopping = threading.Event()

    def stop(_signal: int, _frame: object) -> None:
        stopping.set()
        wake_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping.is_set():
            # Clear before collecting facts so a confirmation arriving during
            # this cycle remains visible to the following wait.  If a wake was
            # consumed between cycles, this immediate reconciliation observes
            # the current hold directly.
            wake_event.clear()
            orchestrator.run_once()
            if stopping.is_set():
                break
            interval_seconds = _reconciliation_interval_seconds(
                args.interval_seconds,
                seal_controller,
            )
            wake_event.wait(interval_seconds)
        return 0
    finally:
        seal_server.close()


if __name__ == "__main__":
    raise SystemExit(main())
