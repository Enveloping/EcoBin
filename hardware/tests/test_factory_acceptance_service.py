from __future__ import annotations

import copy
from http import HTTPStatus
import json
import os
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from factory.acceptance_config import AcceptanceConfiguration
from factory.acceptance_service import (
    AcceptanceCommandController,
    AcceptanceCommandError,
    AcceptanceUnixServer,
)
import factory.acceptance_service as acceptance_service


requires_unix_socket = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"),
    reason="Unix-domain sockets are unavailable on this test platform",
)


class FakeExecutor:
    def __init__(self) -> None:
        self.state: dict[str, object] = {
            "schemaVersion": 1,
            "status": "NOT_RUN",
            "phase": "NOT_RUN",
            "revision": 0,
            "checks": {},
            "secret": "must-not-be-projected",
        }
        self.calls: list[tuple[str, object]] = []
        self.configuration_binding_checks: list[str] = []

    def invalidate_if_hardware_config_changed(
        self, hardware_config_digest: str
    ) -> dict[str, object]:
        self.configuration_binding_checks.append(hardware_config_digest)
        return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        return copy.deepcopy(self.state)

    def _bump(self, phase: str) -> None:
        self.state["phase"] = phase
        self.state["revision"] = int(self.state["revision"]) + 1

    def begin_run(self, **values: object) -> dict[str, object]:
        self.calls.append(("begin_run", values))
        self.state.update(
            {
                "status": "RUNNING",
                "imageReleaseId": values["image_release_id"],
                "hardwareConfigDigest": values["hardware_config_digest"],
            }
        )
        self._bump("RUNNING")
        return self.snapshot()

    def run_action(
        self, action: str, *, operator_area_safe_confirmed: bool
    ) -> dict[str, object]:
        self.calls.append(
            (
                "run_action",
                (action, operator_area_safe_confirmed),
            )
        )
        checks = self.state.setdefault("checks", {})
        assert isinstance(checks, dict)
        checks[action.lower()] = {
            "status": "PASSED",
            "resultCode": f"{action}_SAFE_VERIFIED",
        }
        self._bump(f"{action}_PASSED")
        return self.snapshot()

    def confirm_delivery_area_safe(
        self, *, operator_confirmed: bool
    ) -> dict[str, object]:
        self.calls.append(
            ("confirm_delivery_area_safe", operator_confirmed)
        )
        recovery = self.state.get("recovery")
        disposition = (
            recovery.get("deliveryConfirmationDisposition")
            if isinstance(recovery, dict)
            else None
        )
        recovered_failure = disposition == "FAILED_SAFE_AFTER_CONFIRMATION"
        checks = self.state.setdefault("checks", {})
        assert isinstance(checks, dict)
        delivery = checks.setdefault("delivery", {})
        assert isinstance(delivery, dict)
        delivery.update(
            {
                "status": "FAILED_SAFE" if recovered_failure else "PASSED",
                "resultCode": (
                    "DELIVERY_FAILED_BUT_APPLICATION_RECOVERED"
                    if recovered_failure
                    else "DELIVERY_SAFE_VERIFIED"
                ),
                "operatorAreaSafeConfirmed": operator_confirmed,
            }
        )
        self.state["status"] = "FAILED" if recovered_failure else "RUNNING"
        self.state["recovery"] = None
        self._bump(
            "DELIVERY_RECOVERED_SAFE"
            if recovered_failure
            else "DELIVERY_SAFE_VERIFIED"
        )
        return self.snapshot()

    def __getattr__(self, name: str):
        def action(*args: object, **kwargs: object) -> dict[str, object]:
            self.calls.append((name, (args, kwargs)))
            self._bump(name.upper())
            return self.snapshot()

        return action


def _controller(
    executor: FakeExecutor | None = None,
    *,
    projections: list[dict[str, object]] | None = None,
) -> tuple[AcceptanceCommandController, FakeExecutor]:
    selected = executor or FakeExecutor()
    controller = AcceptanceCommandController(
        selected,  # type: ignore[arg-type]
        AcceptanceConfiguration.from_mapping({}),
        image_release_id="ecobin-zero3-1.0.0",
        boot_id="11111111-2222-3333-4444-555555555555",
        wall_time_trusted=False,
        projection_writer=(projections.append if projections is not None else None),
    )
    return controller, selected


def _request(
    operation: str,
    revision: int,
    parameters: dict[str, object],
) -> dict[str, object]:
    return {
        "operation": operation,
        "expectedRevision": revision,
        "parameters": parameters,
    }


def test_start_requires_explicit_confirmation_and_binds_release_and_config() -> None:
    controller, executor = _controller()

    with pytest.raises(AcceptanceCommandError) as missing:
        controller.execute(
            _request(
                "START",
                0,
                {
                    "confirmOfflineAcceptance": False,
                    "mcuUpdateLineInstalled": True,
                },
            )
        )
    result = controller.execute(
        _request(
            "START",
            0,
            {
                "confirmOfflineAcceptance": True,
                "mcuUpdateLineInstalled": False,
            },
        )
    )

    assert missing.value.code == "OFFLINE_ACCEPTANCE_CONFIRMATION_REQUIRED"
    assert missing.value.status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert result["status"] == "RUNNING"
    begin_values = executor.calls[-1][1]
    assert isinstance(begin_values, dict)
    assert begin_values["image_release_id"] == "ecobin-zero3-1.0.0"
    assert begin_values["hardware_config_digest"] == (
        AcceptanceConfiguration.from_mapping({}).digest()
    )
    assert begin_values["mcu_update_line_installed"] is False


def test_controller_checks_persisted_run_against_loaded_configuration() -> None:
    controller, executor = _controller()

    assert controller.projection()["status"] == "NOT_RUN"
    assert executor.configuration_binding_checks == [
        AcceptanceConfiguration.from_mapping({}).digest()
    ]


def test_custom_reference_request_and_parameter_bound_retry() -> None:
    controller, executor = _controller()
    executor.state.update(status="RUNNING", revision=1, checks={"mcu": {"status": "PASSED"}})
    controller.execute(_request("CAPTURE_EMPTY_WEIGHT", 1, {"confirmScaleEmpty": True, "referenceWeightGrams": 400}))
    assert executor.calls[-1] == ("capture_empty_weight", ((), {"reference_weight_grams": 400}))
    executor.state["checks"]["weight"] = {
        "status": "RUNNING", "resultCode": "WAITING_FOR_REFERENCE_LOAD", "targetDeltaGrams": 400,
    }
    result = controller.execute(_request("CAPTURE_EMPTY_WEIGHT", 1, {"confirmScaleEmpty": True, "referenceWeightGrams": 400}))
    assert result["idempotent"] is True
    assert result["allowedActions"] == ["CAPTURE_LOADED_WEIGHT"]
    before = len(executor.calls)
    with pytest.raises(AcceptanceCommandError, match="WEIGHT_REFERENCE_LOCKED"):
        controller.execute(_request("CAPTURE_EMPTY_WEIGHT", 1, {"confirmScaleEmpty": True, "referenceWeightGrams": 1000}))
    with pytest.raises(AcceptanceCommandError, match="EMPTY_SCALE_CONFIRMATION_REQUIRED"):
        controller.execute(_request("CAPTURE_EMPTY_WEIGHT", 1, {"confirmScaleEmpty": False, "referenceWeightGrams": 400}))
    assert len(executor.calls) == before


def test_projection_contains_only_bounded_measurement_facts() -> None:
    from first_boot.factory_flow import _validate_acceptance_projection

    controller, executor = _controller()
    trace = {"samplesGrams": [100, 110], "readCount": 2, "resultCode": "WEIGHT_READING_NOT_STABLE"}
    executor.state["checks"] = {
        "weight": {"status": "FAILED", "resultCode": "WEIGHT_READING_NOT_STABLE", "sampling": {"empty": trace}, "stableSampleCount": 3},
        "mcu": {"selfTest": {"weightGrams": 100, "infraredBlocked": False, "smokeCode": 0, "secret": "hidden"}},
        "cameras": {"outside": {"captureNonEmpty": True, "source": "private-path"}},
    }
    projection = controller.projection()
    _validate_acceptance_projection(projection)
    assert projection["checks"]["weight"]["sampling"]["empty"] == trace
    assert projection["checks"]["mcu"]["selfTestWeightGrams"] == 100
    assert projection["checks"]["cameras"]["outsideCaptureNonEmpty"] is True
    assert "hidden" not in json.dumps(projection)
    assert "private-path" not in json.dumps(projection)
    executor.state["checks"]["weight"]["sampling"]["empty"]["samplesGrams"] = [0] * 33
    assert "sampling" not in controller.projection()["checks"]["weight"]


@pytest.mark.parametrize("operation, generic, legacy", [
    ("CAPTURE_LOADED_WEIGHT", "confirmReferencePlaced", "confirm500gPlaced"),
    ("CONFIRM_WEIGHT_REMOVED", "confirmReferenceRemoved", "confirm500gRemoved"),
])
def test_weight_confirmation_names_remain_truthful_for_custom_and_legacy_runs(
    operation: str, generic: str, legacy: str,
) -> None:
    controller, executor = _controller()
    executor.state.update(status="RUNNING", revision=2, checks={
        "weight": {"status": "PASSED", "resultCode": "WEIGHT_REFERENCE_WITHIN_TOLERANCE_AND_REMOVED", "targetDeltaGrams": 400},
    })
    with pytest.raises(AcceptanceCommandError, match="ACTION_PARAMETERS_INVALID"):
        controller.execute(_request(operation, 1, {legacy: True}))
    assert controller.execute(_request(operation, 1, {generic: True}))["idempotent"]
    executor.state["checks"]["weight"].update(targetDeltaGrams=500, resultCode="WEIGHT_500G_WITHIN_490_510_AND_REMOVED")
    assert controller.execute(_request(operation, 1, {legacy: True}))["idempotent"]
    assert not executor.calls


def test_start_requires_explicit_update_line_choice_and_binds_false() -> None:
    controller, executor = _controller()

    with pytest.raises(AcceptanceCommandError) as missing:
        controller.execute(
            _request(
                "START",
                0,
                {
                    "confirmOfflineAcceptance": True,
                    "mcuUpdateLineInstalled": None,
                },
            )
        )

    assert missing.value.code == "MCU_UPDATE_LINE_SELECTION_REQUIRED"
    result = controller.execute(
        _request(
            "START",
            0,
            {
                "confirmOfflineAcceptance": True,
                "mcuUpdateLineInstalled": False,
            },
        )
    )
    assert result["status"] == "RUNNING"
    begin_values = executor.calls[-1][1]
    assert isinstance(begin_values, dict)
    assert begin_values["mcu_update_line_installed"] is False


def test_start_rejects_claim_that_absent_remote_update_lines_are_installed() -> None:
    controller, executor = _controller()

    with pytest.raises(AcceptanceCommandError) as unsupported:
        controller.execute(
            _request(
                "START",
                0,
                {
                    "confirmOfflineAcceptance": True,
                    "mcuUpdateLineInstalled": True,
                },
            )
        )

    assert unsupported.value.code == "MCU_REMOTE_UPDATE_LINE_NOT_INSTALLED"
    assert unsupported.value.status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert executor.calls == []


def test_failed_run_restart_also_rejects_absent_remote_update_lines() -> None:
    controller, executor = _controller()
    executor.state.update(status="FAILED", phase="FAILED", revision=4)

    with pytest.raises(AcceptanceCommandError) as unsupported:
        controller.execute(
            _request(
                "RESTART_FAILED_RUN",
                4,
                {
                    "confirmRestartFailedAcceptance": True,
                    "mcuUpdateLineInstalled": True,
                },
            )
        )

    assert unsupported.value.code == "MCU_REMOTE_UPDATE_LINE_NOT_INSTALLED"
    assert unsupported.value.status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert executor.calls == []


def test_build_executor_uses_native_uart_and_separate_durable_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AcceptanceConfiguration.from_mapping({})
    mcu = object()
    cameras = object()
    constructed: dict[str, object] = {}

    def native_for_port(port: str, *, state_path: str) -> object:
        constructed["port"] = port
        constructed["nativeStatePath"] = state_path
        return mcu

    sentinel = object()

    def executor_factory(**values: object) -> object:
        constructed.update(values)
        return sentinel

    monkeypatch.setattr(
        acceptance_service.NativeAcceptanceMcu,
        "for_port",
        native_for_port,
    )
    monkeypatch.setattr(
        acceptance_service,
        "FactoryAcceptanceExecutor",
        executor_factory,
    )
    monkeypatch.setattr(
        acceptance_service,
        "FixedRoleCameraProbe",
        lambda **_values: cameras,
    )
    monkeypatch.setattr(
        acceptance_service,
        "OpenCvCapture",
        lambda: object(),
    )

    result = acceptance_service.build_executor(config)

    assert result is sentinel
    assert constructed["port"] == "/dev/ttyS5"
    assert constructed["nativeStatePath"] == config.native_uart_state_path
    assert constructed["state_path"] == config.state_path
    assert constructed["mcu"] is mcu
    assert constructed["cameras"] is cameras
    bootloader = constructed["bootloader"]
    with pytest.raises(
        acceptance_service.AcceptanceHardwareError,
        match="MCU_REMOTE_UPDATE_LINE_NOT_INSTALLED",
    ):
        bootloader.probe_read_only()


def test_not_applicable_update_line_allows_delivery_step() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RUNNING",
            "revision": 5,
            "checks": {
                "mcu": {"status": "PASSED", "resultCode": "PASSED"},
                "weight": {"status": "PASSED", "resultCode": "PASSED"},
                "cameras": {"status": "PASSED", "resultCode": "PASSED"},
                "upgradeLine": {
                    "status": "NOT_APPLICABLE",
                    "resultCode": "MCU_REMOTE_UPDATE_LINE_NOT_INSTALLED",
                    "prepareSendAttempts": 0,
                    "romWritePerformed": False,
                    "romDeviceId": None,
                },
            },
        }
    )

    assert controller.projection()["allowedActions"] == ["RUN_DELIVERY"]


def test_simulated_mcu_finalize_requires_dedicated_confirmation() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RUNNING",
            "phase": "CLEAN_SAFE_VERIFIED",
            "revision": 12,
            "mcuPeripheralEvidenceMode": "SIMULATED_PERIPHERALS",
            "checks": {
                name: {
                    "status": "PASSED",
                    "resultCode": "PASSED",
                    **(
                        {"operatorAreaSafeConfirmed": True}
                        if name == "delivery"
                        else {"cleanDoorConfirmed": True}
                        if name == "clean"
                        else {}
                    ),
                }
                for name in (
                    "mcu",
                    "weight",
                    "upgradeLine",
                    "cameras",
                    "delivery",
                    "clean",
                )
            },
        }
    )

    with pytest.raises(AcceptanceCommandError) as rejected:
        controller.execute(
            _request(
                "FINALIZE",
                12,
                {
                    "confirmFinalize": True,
                    "confirmSimulatedPeripheralEvidence": False,
                },
            )
        )
    assert rejected.value.code == (
        "SIMULATED_PERIPHERAL_EVIDENCE_CONFIRMATION_REQUIRED"
    )

    controller.execute(
        _request(
            "FINALIZE",
            12,
            {
                "confirmFinalize": True,
                "confirmSimulatedPeripheralEvidence": True,
            },
        )
    )
    assert executor.calls[-1] == (
        "finalize",
        ((), {"confirm_simulated_peripheral_evidence": True}),
    )


def test_clean_rejects_early_door_confirmation_and_only_sends_safe_action_once() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RUNNING",
            "revision": 7,
            "checks": {
                name: {"status": "PASSED", "resultCode": "PASSED"}
                for name in (
                    "mcu",
                    "weight",
                    "upgradeLine",
                    "cameras",
                    "delivery",
                )
            },
        }
    )
    executor.state["checks"]["delivery"][
        "operatorAreaSafeConfirmed"
    ] = True

    with pytest.raises(AcceptanceCommandError) as early:
        controller.execute(
            _request(
                "RUN_CLEAN",
                7,
                {
                    "operatorAreaSafeConfirmed": True,
                    "cleanDoorClosedConfirmed": True,
                },
            )
        )
    result = controller.execute(
        _request("RUN_CLEAN", 7, {"operatorAreaSafeConfirmed": True})
    )

    assert early.value.code == "ACTION_PARAMETERS_INVALID"
    assert executor.calls == [("run_action", ("CLEAN", True))]
    assert result["checks"]["clean"]["status"] == "PASSED"


def test_stale_revision_for_an_already_achieved_action_is_idempotent() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RUNNING",
            "revision": 9,
            "checks": {
                "delivery": {
                    "status": "PASSED",
                    "resultCode": "DELIVERY_SAFE_VERIFIED",
                }
            },
        }
    )

    result = controller.execute(
        _request("RUN_DELIVERY", 8, {"operatorAreaSafeConfirmed": True})
    )

    assert result["idempotent"] is True
    assert executor.calls == []


def test_current_revision_duplicate_delivery_is_idempotent_without_io() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RUNNING",
            "revision": 9,
            "checks": {
                "delivery": {
                    "status": "PASSED",
                    "resultCode": "DELIVERY_SAFE_VERIFIED",
                    "operatorAreaSafeConfirmed": True,
                }
            },
        }
    )

    result = controller.execute(
        _request("RUN_DELIVERY", 9, {"operatorAreaSafeConfirmed": True})
    )

    assert result["idempotent"] is True
    assert executor.calls == []


def test_current_revision_duplicate_upgrade_line_is_idempotent_without_io() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RUNNING",
            "revision": 10,
            "checks": {
                "upgradeLine": {
                    "status": "PASSED",
                    "resultCode": (
                        "F2_BOOT0_NRST_ROM_READ_ONLY_AND_APP_RECOVERY_PASSED"
                    ),
                }
            },
        }
    )

    result = controller.execute(
        _request(
            "CHECK_UPGRADE_LINE",
            10,
            {"confirmReadOnlyBootloaderProbe": True},
        )
    )

    assert result["idempotent"] is True
    assert executor.calls == []


def test_delivery_post_action_confirmation_is_projected_and_executable() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RECOVERY_REQUIRED",
            "phase": "DELIVERY_AWAITING_AREA_CONFIRMATION",
            "revision": 10,
            "checks": {
                "delivery": {
                    "status": "RECOVERY_REQUIRED",
                    "resultCode": (
                        "DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED"
                    ),
                }
            },
            "recovery": {
                "context": "DELIVERY",
                "resultCode": "DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED",
                "hardwareVerified": True,
                "awaitingAreaSafetyConfirmation": True,
            },
        }
    )

    projection = controller.projection()

    assert projection["allowedActions"] == ["CONFIRM_DELIVERY_AREA_SAFE"]
    assert (
        projection["recovery"]["awaitingAreaSafetyConfirmation"] is True
    )
    controller.execute(
        _request(
            "CONFIRM_DELIVERY_AREA_SAFE",
            10,
            {"operatorAreaSafeConfirmed": True},
        )
    )
    assert executor.calls[-1][0] == "confirm_delivery_area_safe"


def test_stale_run_delivery_retry_while_awaiting_confirmation_is_idempotent(
) -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RECOVERY_REQUIRED",
            "phase": "DELIVERY_AWAITING_AREA_CONFIRMATION",
            "revision": 11,
            "checks": {
                "delivery": {
                    "status": "RECOVERY_REQUIRED",
                    "resultCode": (
                        "DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED"
                    ),
                }
            },
            "recovery": {
                "context": "DELIVERY",
                "awaitingAreaSafetyConfirmation": True,
            },
        }
    )

    result = controller.execute(
        _request("RUN_DELIVERY", 10, {"operatorAreaSafeConfirmed": True})
    )

    assert result["idempotent"] is True
    assert result["allowedActions"] == ["CONFIRM_DELIVERY_AREA_SAFE"]
    assert executor.calls == []


def test_recovered_delivery_confirmation_terminates_the_run_as_failed() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RECOVERY_REQUIRED",
            "phase": "DELIVERY_AWAITING_AREA_CONFIRMATION",
            "revision": 12,
            "checks": {
                "delivery": {
                    "status": "RECOVERY_REQUIRED",
                    "resultCode": (
                        "DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED"
                    ),
                }
            },
            "recovery": {
                "context": "DELIVERY",
                "awaitingAreaSafetyConfirmation": True,
                "deliveryConfirmationDisposition": (
                    "FAILED_SAFE_AFTER_CONFIRMATION"
                ),
            },
        }
    )

    result = controller.execute(
        _request(
            "CONFIRM_DELIVERY_AREA_SAFE",
            12,
            {"operatorAreaSafeConfirmed": True},
        )
    )

    assert result["status"] == "FAILED"
    assert result["allowedActions"] == ["RESTART_FAILED_RUN"]
    assert result["checks"]["delivery"]["status"] == "FAILED_SAFE"
    assert executor.calls == [("confirm_delivery_area_safe", True)]


def test_recovered_safe_intermediate_only_allows_failed_report_finalize() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RUNNING",
            "phase": "DELIVERY_RECOVERED_SAFE",
            "revision": 13,
            "checks": {
                **{
                    name: {"status": "PASSED", "resultCode": "PASSED"}
                    for name in (
                        "mcu",
                        "weight",
                        "upgradeLine",
                        "cameras",
                    )
                },
                "delivery": {
                    "status": "FAILED_SAFE",
                    "resultCode": (
                        "DELIVERY_FAILED_BUT_APPLICATION_RECOVERED"
                    ),
                    "sendAttempts": 1,
                    "operatorAreaSafeConfirmed": True,
                },
            },
            "recovery": None,
        }
    )

    projection = controller.projection()

    assert projection["allowedActions"] == ["FINALIZE"]
    controller.execute(_request("FINALIZE", 13, {"confirmFinalize": True}))
    assert executor.calls == [("finalize", ((), {}))]


def test_recovered_upgrade_line_intermediate_never_offers_f2_again() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RUNNING",
            "phase": "UPGRADE_LINE_RECOVERED_SAFE",
            "revision": 14,
            "checks": {
                "mcu": {"status": "PASSED", "resultCode": "PASSED"},
                "upgradeLine": {
                    "status": "FAILED_SAFE",
                    "resultCode": (
                        "UPGRADE_LINE_FAILED_BUT_APPLICATION_RECOVERED"
                    ),
                    "sendAttempts": 1,
                },
            },
        }
    )

    projection = controller.projection()

    assert projection["allowedActions"] == ["FINALIZE"]
    with pytest.raises(AcceptanceCommandError) as blocked:
        controller.execute(
            _request(
                "CHECK_UPGRADE_LINE",
                14,
                {"confirmReadOnlyBootloaderProbe": True},
            )
        )
    assert blocked.value.code == "ACTION_NOT_ALLOWED_IN_CURRENT_STATE"
    assert executor.calls == []


@pytest.mark.parametrize(
    ("action_name", "action_status", "result_code", "blocked_operation"),
    (
        (
            "delivery",
            "PASSED",
            "DELIVERY_SAFE_VERIFIED",
            "RUN_CLEAN",
        ),
        (
            "delivery",
            "FAILED_SAFE",
            "DELIVERY_FAILED_BUT_APPLICATION_RECOVERED",
            "FINALIZE",
        ),
        (
            "clean",
            "PASSED",
            "CLEAN_SAFE_VERIFIED",
            "FINALIZE",
        ),
        (
            "clean",
            "FAILED_SAFE",
            "CLEAN_FAILED_BUT_APPLICATION_RECOVERED",
            "FINALIZE",
        ),
    ),
)
def test_missing_post_action_confirmation_never_offers_next_physical_step_or_finalize(
    action_name: str,
    action_status: str,
    result_code: str,
    blocked_operation: str,
) -> None:
    controller, executor = _controller()
    checks = {
        name: {"status": "PASSED", "resultCode": "PASSED"}
        for name in ("mcu", "weight", "upgradeLine", "cameras")
    }
    if action_name == "clean":
        checks["delivery"] = {
            "status": "PASSED",
            "resultCode": "DELIVERY_SAFE_VERIFIED",
            "operatorAreaSafeConfirmed": True,
        }
    checks[action_name] = {
        "status": action_status,
        "resultCode": result_code,
        "sendAttempts": 1,
    }
    executor.state.update(
        {
            "status": "RUNNING",
            "phase": "LEGACY_ACTION_RESULT",
            "revision": 15,
            "checks": checks,
            "recovery": None,
            "activeAction": None,
        }
    )

    projection = controller.projection()

    assert projection["allowedActions"] == []
    parameters = (
        {"operatorAreaSafeConfirmed": True}
        if blocked_operation == "RUN_CLEAN"
        else {"confirmFinalize": True}
    )
    with pytest.raises(AcceptanceCommandError) as blocked:
        controller.execute(_request(blocked_operation, 15, parameters))
    assert blocked.value.code == "ACTION_NOT_ALLOWED_IN_CURRENT_STATE"
    assert executor.calls == []


def test_recovered_legacy_safety_failure_only_allows_failed_finalize() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RUNNING",
            "phase": "LEGACY_ACTION_SAFETY_FAILURE_READY",
            "revision": 16,
            "checks": {
                "delivery": {
                    "status": "FAILED",
                    "resultCode": (
                        "LEGACY_ACTION_REQUIRES_NEW_ACCEPTANCE_RUN"
                    ),
                }
            },
            "recovery": None,
            "activeAction": None,
        }
    )

    assert controller.projection()["allowedActions"] == ["FINALIZE"]
    controller.execute(
        _request("FINALIZE", 16, {"confirmFinalize": True})
    )
    assert executor.calls == [("finalize", ((), {}))]


def test_interrupted_failed_report_finalization_only_allows_finalize_retry() -> None:
    controller, executor = _controller()
    executor.state.update(
        {
            "status": "RUNNING",
            "phase": "FINALIZING_REPORT",
            "pendingFinalStatus": "FAILED",
            "revision": 17,
            "checks": {
                "delivery": {
                    "status": "FAILED",
                    "resultCode": (
                        "LEGACY_ACTION_REQUIRES_NEW_ACCEPTANCE_RUN"
                    ),
                }
            },
            "recovery": None,
            "activeAction": None,
        }
    )

    assert controller.projection()["allowedActions"] == ["FINALIZE"]
    controller.execute(
        _request("FINALIZE", 17, {"confirmFinalize": True})
    )
    assert executor.calls == [("finalize", ((), {}))]


def test_stale_revision_for_unachieved_action_conflicts_without_hardware_io() -> None:
    controller, executor = _controller()
    executor.state.update({"status": "RUNNING", "revision": 2})

    with pytest.raises(AcceptanceCommandError) as conflict:
        controller.execute(_request("CHECK_MCU", 1, {}))

    assert conflict.value.code == "ACCEPTANCE_REVISION_CONFLICT"
    assert conflict.value.status == HTTPStatus.CONFLICT
    assert executor.calls == []


def test_current_revision_operation_outside_allowed_actions_is_rejected() -> None:
    controller, executor = _controller()
    executor.state.update({"status": "RUNNING", "revision": 3})

    with pytest.raises(AcceptanceCommandError) as rejected:
        controller.execute(
            _request(
                "RUN_DELIVERY",
                3,
                {"operatorAreaSafeConfirmed": True},
            )
        )

    assert rejected.value.code == "ACTION_NOT_ALLOWED_IN_CURRENT_STATE"
    assert rejected.value.status == HTTPStatus.CONFLICT
    assert executor.calls == []


def test_concurrent_physical_mutation_returns_busy_instead_of_queueing() -> None:
    controller, executor = _controller()
    assert controller._action_lock.acquire(blocking=False)  # intentional race boundary
    try:
        with pytest.raises(AcceptanceCommandError) as busy:
            controller.execute(
                _request(
                    "START",
                    0,
                    {
                        "confirmOfflineAcceptance": True,
                        "mcuUpdateLineInstalled": True,
                    },
                )
            )
    finally:
        controller._action_lock.release()

    assert busy.value.code == "ACCEPTANCE_EXECUTOR_BUSY"
    assert busy.value.status == HTTPStatus.CONFLICT
    assert executor.calls == []


def test_public_projection_is_allowlisted_and_never_copies_executor_secrets() -> None:
    written: list[dict[str, object]] = []
    controller, executor = _controller(projections=written)
    executor.state.update(
        {
            "status": "RECOVERY_REQUIRED",
            "phase": "ACTION_RECOVERY_REQUIRED",
            "revision": 4,
            "mcuIdentity": {
                "fixedFrameRevision": 2,
                "firmwareVersion": "v1.2.3",
                "firmwareVersionCode": 0x010203,
                "firmwareIdentityHex": "0123456789abcdef",
                "deviceKey": "must-not-pass",
            },
            "recovery": {
                "context": "CLEAN",
                "resultCode": "CLEAN_RESULT_UNCERTAIN",
                "hardwareVerified": False,
                "privateDiagnostic": "must-not-pass",
            },
        }
    )

    projection = controller.projection(idempotent=True)

    assert projection["idempotent"] is True
    assert written[-1]["idempotent"] is False
    encoded = str(written[-1])
    assert "must-not-be-projected" not in encoded
    assert "privateDiagnostic" not in encoded
    # MCU identity is non-secret acceptance evidence, but arbitrary nested keys
    # must not cross the root-to-web privilege boundary.
    assert "deviceKey" not in encoded


def _unix_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AcceptanceUnixServer:
    controller, _executor = _controller()
    monkeypatch.setattr(
        acceptance_service.grp,
        "getgrnam",
        lambda _name: SimpleNamespace(gr_gid=os.getgid()),
    )
    monkeypatch.setattr(acceptance_service.os, "chown", lambda *_args: None)
    return AcceptanceUnixServer(
        tmp_path / "acceptance.sock",
        controller,
        portal_group="ecobin-factory-web",
    )


@requires_unix_socket
def test_unix_executor_runs_actions_on_the_server_owner_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The native UART is opened before serving and stays thread-affine."""

    monkeypatch.setattr(
        acceptance_service.grp,
        "getgrnam",
        lambda _name: SimpleNamespace(gr_gid=os.getgid()),
    )
    monkeypatch.setattr(acceptance_service.os, "chown", lambda *_args: None)
    ready = threading.Event()
    holder: dict[str, object] = {}

    class ThreadBoundController:
        def __init__(self) -> None:
            self.owner = threading.get_ident()

        def execute(self, request: object) -> dict[str, object]:
            assert threading.get_ident() == self.owner
            return {"request": request, "sameOwnerThread": True}

    def serve() -> None:
        server = AcceptanceUnixServer(
            tmp_path / "acceptance.sock",
            ThreadBoundController(),  # type: ignore[arg-type]
            portal_group="ecobin-factory-web",
        )
        holder["server"] = server
        ready.set()
        try:
            server.serve_forever(poll_interval=0.01)
        finally:
            server.server_close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=1)
    server = holder["server"]
    assert isinstance(server, AcceptanceUnixServer)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(str(tmp_path / "acceptance.sock"))
            client.sendall(b'{"operation":"CHECK_MCU"}\n')
            response = json.loads(client.makefile("rb").readline().decode("ascii"))

        assert response == {
            "data": {
                "request": {"operation": "CHECK_MCU"},
                "sameOwnerThread": True,
            },
            "ok": True,
        }
    finally:
        server.shutdown()
        thread.join(timeout=1)


@requires_unix_socket
def test_slow_unix_client_receives_stable_timeout_and_cannot_hold_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        acceptance_service, "CLIENT_SOCKET_TIMEOUT_SECONDS", 0.05
    )
    server = _unix_server(tmp_path, monkeypatch)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(1)
        client.connect(str(tmp_path / "acceptance.sock"))

        response = json.loads(client.makefile("rb").readline().decode("ascii"))

        assert response == {
            "error": "IPC_REQUEST_TIMEOUT",
            "httpStatus": HTTPStatus.REQUEST_TIMEOUT.value,
            "ok": False,
        }
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


@requires_unix_socket
def test_unix_executor_serializes_complete_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        acceptance_service.grp,
        "getgrnam",
        lambda _name: SimpleNamespace(gr_gid=os.getgid()),
    )
    monkeypatch.setattr(acceptance_service.os, "chown", lambda *_args: None)
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    class SerialController:
        def execute(self, request: object) -> dict[str, object]:
            if request == {"request": 1}:
                first_started.set()
                assert release_first.wait(timeout=1)
            elif request == {"request": 2}:
                second_started.set()
            return {"request": request}

    server = AcceptanceUnixServer(
        tmp_path / "acceptance.sock",
        SerialController(),  # type: ignore[arg-type]
        portal_group="ecobin-factory-web",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        first.settimeout(1)
        first.connect(str(tmp_path / "acceptance.sock"))
        first.sendall(b'{"request":1}\n')
        assert first_started.wait(timeout=1)

        second.settimeout(1)
        second.connect(str(tmp_path / "acceptance.sock"))
        second.sendall(b'{"request":2}\n')
        assert not second_started.wait(timeout=0.05)

        release_first.set()
        first_response = json.loads(
            first.makefile("rb").readline().decode("ascii")
        )
        second_response = json.loads(
            second.makefile("rb").readline().decode("ascii")
        )
        assert first_response["ok"] is True
        assert second_response["ok"] is True
        assert second_started.is_set()
    finally:
        release_first.set()
        first.close()
        second.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()
