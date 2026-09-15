from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any, Callable
from urllib.parse import quote

from factory_progress import (
    validate_enrollment_progress,
    validate_runtime_progress,
)
from factory_seal.runtime_health import validate_runtime_services
from factory.acceptance_measurements import valid_sampling
from .cellular_status import parse_cellular_status

from .atomic_json import AtomicJsonFile, OwnershipSetter, root_group_owner
from .model import FirstBootFacts, FirstBootStage, normalize_error_code


FLOW_NODE_IDS = (
    "BOOT_AND_PORTAL",
    "LOCAL_HARDWARE_ACCEPTANCE",
    "CELLULAR_AND_TIME",
    "ENROLLMENT_AND_CREDENTIALS",
    "RUNTIME_AND_MQTT",
    "FACTORY_BAGS",
    "CLOUD_EVIDENCE",
    "CLOUD_DECISION_AND_AUTHORIZATION",
    "FACTORY_SEAL",
)
FLOW_STATES = {
    "PENDING",
    "ACTIVE",
    "WAITING_OPERATOR",
    "BLOCKED",
    "COMPLETED",
    "UNKNOWN",
}
_TOP_FIELDS = {"schemaVersion", "currentNode", "overallState", "nodes"}
_NODE_FIELDS = {"id", "state", "detailCode", "errorCode", "steps"}
_STEP_FIELDS = {"id", "state", "detailCode", "errorCode"}
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_P7_FIELDS = {
    "schemaVersion",
    "executorAvailable",
    "status",
    "phase",
    "revision",
    "idempotent",
    "imageReleaseId",
    "hardwareConfigSummary",
    "mcuIdentity",
    "mcuUpdateLineInstalled",
    "mcuPeripheralEvidenceMode",
    "checks",
    "recovery",
    "cameraReview",
    "allowedActions",
}
_P7_CHECK_NAMES = {
    "mcu",
    "weight",
    "upgradeLine",
    "cameras",
    "delivery",
    "clean",
}
_P7_CHECK_OPTIONAL_FIELDS = {
    "emptyWeightGrams",
    "loadedWeightGrams",
    "removedWeightGrams",
    "deltaGrams",
    "targetDeltaGrams",
    "toleranceGrams",
    "stableSampleCount",
    "stableMaxSpreadGrams",
    "sampleIntervalMs",
    "sampleTimeoutMs",
    "sampling",
    "selfTestWeightGrams",
    "selfTestInfraredBlocked",
    "selfTestSmokeCode",
    "selfTestSmokeState",
    "selfTestSmokeSensorHealth",
    "selfTestFullnessSensorKind",
    "selfTestFullnessReadStatus",
    "selfTestFullnessDistanceMm",
    "selfTestFullnessDistanceThresholdMm",
    "selfTestFullnessBlocked",
    "outsideCaptureNonEmpty",
    "insideCaptureNonEmpty",
    "outsideRoleConfirmed",
    "insideRoleConfirmed",
    "sendAttempts",
    "prepareSendAttempts",
    "romWritePerformed",
    "romDeviceId",
    "cleanDoorConfirmed",
    "operatorAreaSafeConfirmed",
    "result",
}
_P7_STATUSES = {
    "NOT_RUN",
    "RUNNING",
    "PASSED",
    "FAILED",
    "RECOVERY_REQUIRED",
}
_SEAL_FIELDS = {
    "authorized",
    "confirmAllowed",
    "statusCode",
    "acceptanceGeneration",
    "authorizationBindingSha256",
    "runtimeServices",
}
_SEAL_STATUS_CODES = {
    "CLOUD_ACCEPTANCE_REQUIRED",
    "SEAL_READY",
    "IMAGE_RELEASE_INVALID",
    "FACTORY_REPORT_INVALID",
    "FACTORY_SEAL_LOCAL_FACT_CHANGED",
    "ENROLLMENT_CLEANUP_REQUIRED",
    "DEVICE_CREDENTIALS_INVALID",
    "HANDOFF_SAFE_REQUIRED",
    "DEVICE_CAPABILITIES_INVALID",
    "MAINTENANCE_BUSY",
    "RUNTIME_NOT_HEALTHY",
    "SEALED_RESPONSE_PENDING",
    "SEALED_CLEANUP_PENDING",
    "SEALED",
    "SEALED_FACT_INVALID",
    "SEALED_FACT_MISSING",
    "FACTORY_SEAL_NOT_AVAILABLE",
}


@dataclass(frozen=True, slots=True)
class FactoryFlowPaths:
    acceptance: Path = Path("/run/ecobin/factory-portal/acceptance.json")
    cellular: Path = Path("/run/ecobin/cellular-uplink/status.json")
    enrollment: Path = Path("/run/ecobin/enrollment/progress.json")
    runtime: Path = Path("/run/ecobin/hardware/factory-progress.json")
    edge_store: Path = Path("/var/lib/ecobin/hardware/edge.db")
    output: Path = Path("/run/ecobin/factory-portal/flow.json")


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    available: bool
    value: dict[str, Any]


class FactoryFlowProjector:
    """Publish a bounded, secret-free view of the complete factory journey."""

    def __init__(
        self,
        paths: FactoryFlowPaths = FactoryFlowPaths(),
        *,
        group_id: int | None = None,
        owner: OwnershipSetter | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if owner is None:
            if group_id is None:
                raise ValueError("group_id is required for the flow projection")
            owner = root_group_owner(group_id)
        self._paths = paths
        self._monotonic = monotonic or time.monotonic
        self._file = AtomicJsonFile(
            paths.output,
            mode=0o640,
            directory_mode=0o750,
            owner=owner,
            maximum_bytes=32 * 1024,
        )

    def publish(
        self,
        stage: FirstBootStage,
        facts: FirstBootFacts,
        *,
        error_code: str,
        seal: dict[str, object],
    ) -> dict[str, object]:
        acceptance = _read_validated_json(
            self._paths.acceptance,
            _validate_acceptance_projection,
            maximum_bytes=64 * 1024,
        )
        cellular = _read_validated_json(
            self._paths.cellular,
            _validate_cellular_projection,
            maximum_bytes=512,
        )
        enrollment = _read_validated_json(
            self._paths.enrollment,
            validate_enrollment_progress,
            maximum_bytes=2048,
        )
        runtime = _read_validated_json(
            self._paths.runtime,
            validate_runtime_progress,
            maximum_bytes=4096,
        )
        durable = _read_acceptance_delivery(self._paths.edge_store)
        seal_source = _validate_seal_source(seal)
        projection = self._build(
            stage,
            facts,
            error_code=error_code,
            seal=seal_source,
            acceptance=acceptance,
            cellular=cellular,
            enrollment=enrollment,
            runtime=runtime,
            durable=durable,
        )
        validate_factory_flow_projection(projection)
        self._file.write_object(projection)
        return projection

    def _build(
        self,
        stage: FirstBootStage,
        facts: FirstBootFacts,
        *,
        error_code: str,
        seal: _SourceSnapshot,
        acceptance: _SourceSnapshot,
        cellular: _SourceSnapshot,
        enrollment: _SourceSnapshot,
        runtime: _SourceSnapshot,
        durable: _SourceSnapshot,
    ) -> dict[str, object]:
        nodes: list[dict[str, object]] = []
        public_error = _code(error_code, "NONE")
        acceptance_available = acceptance.available
        cellular_available = cellular.available
        enrollment_available = enrollment.available
        runtime_available = runtime.available
        durable_available = durable.available
        seal_available = seal.available
        acceptance_value = acceptance.value
        cellular_value = cellular.value
        enrollment_value = enrollment.value
        runtime_value = runtime.value
        durable_value = durable.value
        seal_value = seal.value

        boot_complete = facts.system_prepared and facts.factory_portal_ready
        boot_state = "COMPLETED" if boot_complete else "BLOCKED" if public_error != "NONE" else "ACTIVE"
        nodes.append(_node(
            "BOOT_AND_PORTAL",
            boot_state,
            "BOOT_READY" if boot_complete else stage.value,
            public_error if boot_state == "BLOCKED" else "NONE",
            (
                _bool_step("SYSTEM_PREPARED", facts.system_prepared),
                _bool_step("FACTORY_PORTAL_READY", facts.factory_portal_ready),
            ),
        ))

        p7_status = _code(
            acceptance_value.get("status"),
            facts.factory_test_status.value,
        )
        p7_phase = _code(acceptance_value.get("phase"), "NOT_RUN")
        if not boot_complete:
            p7_state = "PENDING"
        elif not acceptance_available:
            p7_state = "UNKNOWN"
        elif p7_status == "PASSED" and facts.factory_report_valid:
            p7_state = "COMPLETED"
        elif p7_status in {"FAILED", "RECOVERY_REQUIRED"}:
            p7_state = "BLOCKED"
        elif p7_status == "RUNNING":
            p7_state = "ACTIVE"
        else:
            p7_state = "WAITING_OPERATOR"
        p7_steps = []
        checks = acceptance_value.get("checks")
        checks = checks if isinstance(checks, dict) else {}
        for check_id, source_name in (
            ("MCU", "mcu"),
            ("WEIGHT", "weight"),
            ("MCU_UPDATE_LINE", "upgradeLine"),
            ("CAMERAS", "cameras"),
            ("DELIVERY", "delivery"),
            ("CLEAN", "clean"),
        ):
            if acceptance_available:
                check = checks.get(source_name)
                check = check if isinstance(check, dict) else {}
                check_status = _code(check.get("status"), "NOT_RUN")
                check_result = _code(check.get("resultCode"), "NOT_RUN")
                p7_steps.append(
                    _check_step(check_id, check_status, check_result)
                )
            else:
                p7_steps.append(_unknown_step(check_id))
        p7_steps.append(_bool_step("REPORT", facts.factory_report_valid))
        nodes.append(_node(
            "LOCAL_HARDWARE_ACCEPTANCE",
            p7_state,
            "STATUS_UNAVAILABLE" if p7_state == "UNKNOWN" else p7_phase,
            public_error if p7_state == "BLOCKED" else "NONE",
            tuple(p7_steps),
        ))

        cellular_result = _code(
            cellular_value.get("resultCode"),
            "STATUS_UNAVAILABLE",
        )
        cellular_complete = facts.uplink_ready and facts.time_trusted
        if p7_state != "COMPLETED":
            cellular_state = "PENDING"
        elif not cellular_available or cellular_result == "STATUS_UNAVAILABLE":
            cellular_state = "UNKNOWN"
        elif cellular_complete:
            cellular_state = "COMPLETED"
        elif cellular_result in {
            "NONE",
            "TIME_SYNC_PENDING",
            "TIME_SYNC_IN_PROGRESS",
            "FACTORY_TEST_GATE_CLOSED",
            "CELLULAR_NETWORK_REGISTRATION_PENDING",
            "CELLULAR_PACKET_SERVICE_PENDING",
        }:
            cellular_state = "ACTIVE"
        else:
            cellular_state = "BLOCKED"
        nodes.append(_node(
            "CELLULAR_AND_TIME",
            cellular_state,
            "STATUS_UNAVAILABLE"
            if cellular_state == "UNKNOWN"
            else "CELLULAR_AND_TIME_READY"
            if cellular_complete
            else cellular_result,
            cellular_result if cellular_state == "BLOCKED" else "NONE",
            (
                _bool_step("AIR780E_PROFILE", facts.cellular_profile_active),
                _bool_step("CELLULAR_UPLINK", facts.uplink_ready),
                _bool_step("TRUSTED_TIME", facts.time_trusted),
            ),
        ))

        enrollment_phase = _code(
            enrollment_value.get("phase"),
            "NOT_STARTED",
        )
        enrollment_error = _code(
            enrollment_value.get("lastErrorCode"),
            "NONE",
        )
        if cellular_state != "COMPLETED":
            enrollment_state = "PENDING"
        elif not enrollment_available:
            enrollment_state = "UNKNOWN"
        elif facts.enrollment_complete:
            enrollment_state = "COMPLETED"
        elif (
            enrollment_phase == "FAILED"
            and enrollment_value.get("retryable") is False
        ):
            enrollment_state = "BLOCKED"
        else:
            enrollment_state = "ACTIVE"
        nodes.append(_node(
            "ENROLLMENT_AND_CREDENTIALS",
            enrollment_state,
            "STATUS_UNAVAILABLE"
            if enrollment_state == "UNKNOWN"
            else "ENROLLMENT_COMPLETE"
            if facts.enrollment_complete
            else enrollment_phase,
            enrollment_error if enrollment_state == "BLOCKED" else "NONE",
            _enrollment_steps(enrollment_phase, facts.enrollment_complete),
        ))

        runtime_fresh = runtime_available and _runtime_is_fresh(
            runtime_value,
            self._monotonic(),
        )
        service_state = _code(
            runtime_value.get("serviceState"),
            "STATUS_UNAVAILABLE",
        )
        uart_state = _code(
            runtime_value.get("uartState"),
            "STATUS_UNAVAILABLE",
        )
        mqtt_state = _code(
            runtime_value.get("mqttState"),
            "STATUS_UNAVAILABLE",
        )
        runtime_error = _code(
            runtime_value.get("lastErrorCode"),
            "NONE",
        )
        runtime_complete = bool(
            facts.handoff_safe
            and runtime_fresh
            and service_state == "RUNNING"
            and uart_state == "READY"
            and mqtt_state == "CONNECTED"
        )
        runtime_blocking_error = _runtime_blocking_error(
            service_state,
            uart_state,
            mqtt_state,
            runtime_error,
        )
        if enrollment_state != "COMPLETED":
            runtime_state = "PENDING"
        elif not runtime_available:
            runtime_state = "UNKNOWN"
        elif not runtime_fresh:
            runtime_state = "UNKNOWN"
        elif runtime_complete:
            runtime_state = "COMPLETED"
        elif runtime_blocking_error is not None:
            runtime_state = "BLOCKED"
        else:
            runtime_state = "ACTIVE"
        runtime_detail = (
            "RUNTIME_READY"
            if runtime_complete
            else "STATUS_UNAVAILABLE"
            if not runtime_available
            else "RUNTIME_STATUS_STALE"
            if not runtime_fresh
            else runtime_blocking_error
            if runtime_blocking_error is not None
            else service_state
        )
        nodes.append(_node(
            "RUNTIME_AND_MQTT",
            runtime_state,
            runtime_detail,
            runtime_blocking_error if runtime_state == "BLOCKED" else "NONE",
            (
                _bool_step("UART_HANDOFF", facts.handoff_safe),
                _state_step("HARDWARE_SERVICE", service_state, "RUNNING"),
                _state_step("UART_READY", uart_state, "READY"),
                _state_step("MQTT_CONNECTED", mqtt_state, "CONNECTED"),
            ),
        ))

        command = durable_value.get("command")
        command = command if isinstance(command, dict) else {}
        command_state = _code(command.get("state"), "NOT_RECEIVED")
        command_received = bool(command)
        entry_qr_state = _code(
            durable_value.get("deviceEntryUrlState"),
            "STATUS_UNAVAILABLE",
        )
        if runtime_state != "COMPLETED":
            bags_state = "PENDING"
        elif not durable_available:
            bags_state = "UNKNOWN"
        elif command_received:
            bags_state = "COMPLETED"
        elif entry_qr_state == "APPLIED":
            bags_state = "WAITING_OPERATOR"
        elif entry_qr_state == "FAILED":
            bags_state = "BLOCKED"
        else:
            bags_state = "ACTIVE"
        if command_received or entry_qr_state == "APPLIED":
            entry_qr_step = _bool_step("DEVICE_ENTRY_QR", True)
        elif entry_qr_state == "FAILED":
            entry_qr_step = _step(
                "DEVICE_ENTRY_QR",
                "BLOCKED",
                "DEVICE_ENTRY_QR_FAILED",
                "DEVICE_ENTRY_QR_FAILED",
            )
        else:
            entry_qr_step = _bool_step("DEVICE_ENTRY_QR", False)
        nodes.append(_node(
            "FACTORY_BAGS",
            bags_state,
            "STATUS_UNAVAILABLE"
            if bags_state == "UNKNOWN"
            else "P8_REQUEST_RECEIVED"
            if command_received
            else "SCAN_DEVICE_AND_FACTORY_BAGS"
            if entry_qr_state == "APPLIED"
            else "DEVICE_ENTRY_QR_FAILED"
            if entry_qr_state == "FAILED"
            else "PREPARING_DEVICE_ENTRY_QR",
            "DEVICE_ENTRY_QR_FAILED"
            if bags_state == "BLOCKED"
            else "NONE",
            (
                entry_qr_step,
                _bool_step("P8_REQUEST", command_received)
                if durable_available
                else _unknown_step("P8_REQUEST")
            ),
        ))

        event = durable_value.get("event")
        event = event if isinstance(event, dict) else {}
        event_state = _code(event.get("state"), "NOT_RECORDED")
        platform_accepted = bool(
            event and event.get("platformAccepted") is True
        )
        event_confirmed = bool(
            event
            and (
                event_state == "CONFIRMED"
                or event.get("confirmed") is True
            )
        )
        p8_phase = _code(runtime_value.get("p8Phase"), "IDLE")
        command_error = _code(command.get("lastError"), "NONE")
        p8_started = p8_phase != "IDLE"
        if not command_received and not p8_started:
            evidence_state = "PENDING"
        elif not durable_available:
            evidence_state = "UNKNOWN"
        elif p8_phase == "FAILED":
            evidence_state = "BLOCKED"
        elif command_state in {"FAILED", "REJECTED", "RECOVERY_REQUIRED"}:
            evidence_state = "BLOCKED"
        elif event_state == "DEAD":
            evidence_state = "BLOCKED"
        elif event_confirmed:
            evidence_state = "COMPLETED"
        else:
            evidence_state = "ACTIVE"
        evidence_error = (
            runtime_error
            if p8_phase == "FAILED" and runtime_error != "NONE"
            else command_error
            if command_error != "NONE"
            else "ACCEPTANCE_EVIDENCE_DELIVERY_DEAD"
            if event_state == "DEAD"
            else "NONE"
        )
        evidence_detail = (
            "STATUS_UNAVAILABLE"
            if evidence_state == "UNKNOWN"
            else "EVIDENCE_CONFIRMED"
            if event_confirmed
            else "FAILED"
            if p8_phase == "FAILED"
            else "DEAD"
            if event_state == "DEAD"
            else "WAITING_BACKEND_CONFIRMATION"
            if platform_accepted
            else "WAITING_ONENET_ACCEPTANCE"
            if event_state != "NOT_RECORDED"
            else p8_phase
            if p8_phase != "IDLE"
            else event_state
        )
        nodes.append(_node(
            "CLOUD_EVIDENCE",
            evidence_state,
            evidence_detail,
            evidence_error if evidence_state == "BLOCKED" else "NONE",
            _p8_steps(
                p8_phase,
                runtime_error,
                event_state,
                platform_accepted,
                event_confirmed,
            ),
        ))

        seal_authorized = bool(
            seal_available
            and seal_value.get("authorized") is True
            and isinstance(seal_value.get("acceptanceGeneration"), int)
            and not isinstance(
                seal_value.get("acceptanceGeneration"),
                bool,
            )
            and seal_value.get("acceptanceGeneration", 0) > 0
        )
        seal_code = _code(
            seal_value.get("statusCode"),
            "FACTORY_SEAL_NOT_AVAILABLE",
        )
        if evidence_state != "COMPLETED":
            cloud_state = "PENDING"
            cloud_detail = "WAITING_ACCEPTANCE_EVIDENCE"
        elif not seal_available:
            cloud_state = "UNKNOWN"
            cloud_detail = "STATUS_UNAVAILABLE"
        elif seal_authorized:
            cloud_state = "COMPLETED"
            cloud_detail = "CLOUD_ACCEPTANCE_PASSED"
        else:
            cloud_state = "ACTIVE"
            cloud_detail = "WAITING_CLOUD_DECISION"
        nodes.append(_node(
            "CLOUD_DECISION_AND_AUTHORIZATION",
            cloud_state,
            cloud_detail,
            "NONE",
            (
                _bool_step("EVIDENCE_CONFIRMED", event_confirmed),
                _bool_step(
                    "CURRENT_GENERATION_AUTHORIZED",
                    seal_authorized,
                )
                if seal_available
                else _unknown_step("CURRENT_GENERATION_AUTHORIZED"),
            ),
        ))

        sealing = seal_code in {
            "SEALED_RESPONSE_PENDING",
            "SEALED_CLEANUP_PENDING",
            "SEALED",
        }
        confirm_allowed = seal_value.get("confirmAllowed") is True
        if cloud_state != "COMPLETED":
            final_state = "PENDING"
        elif sealing:
            final_state = "COMPLETED"
        elif confirm_allowed:
            final_state = "WAITING_OPERATOR"
        elif seal_authorized:
            final_state = "BLOCKED"
        else:
            final_state = "PENDING"
        nodes.append(_node(
            "FACTORY_SEAL",
            final_state,
            seal_code,
            seal_code if final_state == "BLOCKED" else "NONE",
            (
                _bool_step("SEAL_AUTHORIZATION", seal_authorized),
                _bool_step("LOCAL_CONFIRMATION_AVAILABLE", confirm_allowed),
                _bool_step("SEAL_MARKER_SAVED", sealing),
            ),
        ))

        current = nodes[-1]
        for candidate in nodes:
            if candidate["state"] != "COMPLETED":
                current = candidate
                break
        overall = str(current["state"])
        if all(node["state"] == "COMPLETED" for node in nodes):
            overall = "COMPLETED"
        return {
            "schemaVersion": 1,
            "currentNode": current["id"],
            "overallState": overall,
            "nodes": nodes,
        }


def validate_factory_flow_projection(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _TOP_FIELDS:
        raise ValueError("factory flow projection fields are invalid")
    if value.get("schemaVersion") != 1:
        raise ValueError("factory flow projection version is invalid")
    nodes = value.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != len(FLOW_NODE_IDS):
        raise ValueError("factory flow projection nodes are invalid")
    for expected_id, node in zip(FLOW_NODE_IDS, nodes, strict=True):
        if not isinstance(node, dict) or set(node) != _NODE_FIELDS:
            raise ValueError("factory flow node fields are invalid")
        if node.get("id") != expected_id or node.get("state") not in FLOW_STATES:
            raise ValueError("factory flow node value is invalid")
        _require_code(node.get("detailCode"))
        _require_code(node.get("errorCode"))
        steps = node.get("steps")
        if not isinstance(steps, list) or len(steps) > 12:
            raise ValueError("factory flow steps are invalid")
        for step in steps:
            if not isinstance(step, dict) or set(step) != _STEP_FIELDS:
                raise ValueError("factory flow step fields are invalid")
            _require_code(step.get("id"))
            if step.get("state") not in FLOW_STATES:
                raise ValueError("factory flow step state is invalid")
            _require_code(step.get("detailCode"))
            _require_code(step.get("errorCode"))
    if value.get("currentNode") not in FLOW_NODE_IDS:
        raise ValueError("factory flow current node is invalid")
    if value.get("overallState") not in FLOW_STATES:
        raise ValueError("factory flow overall state is invalid")
    return json.loads(json.dumps(value))


def empty_factory_flow_projection() -> dict[str, object]:
    nodes = [
        _node(node_id, "UNKNOWN" if index == 0 else "PENDING", "STATUS_UNAVAILABLE", "NONE", ())
        for index, node_id in enumerate(FLOW_NODE_IDS)
    ]
    return {
        "schemaVersion": 1,
        "currentNode": FLOW_NODE_IDS[0],
        "overallState": "UNKNOWN",
        "nodes": nodes,
    }


def _node(
    node_id: str,
    state: str,
    detail_code: str,
    error_code: str,
    steps: tuple[dict[str, str], ...],
) -> dict[str, object]:
    return {
        "id": node_id,
        "state": state,
        "detailCode": _code(detail_code, "STATUS_UNAVAILABLE"),
        "errorCode": _code(error_code, "NONE"),
        "steps": list(steps),
    }


def _bool_step(step_id: str, complete: bool) -> dict[str, str]:
    return _step(
        step_id,
        "COMPLETED" if complete else "PENDING",
        "COMPLETED" if complete else "PENDING",
    )


def _unknown_step(step_id: str) -> dict[str, str]:
    return _step(step_id, "UNKNOWN", "STATUS_UNAVAILABLE")


def _state_step(step_id: str, observed: str, completed_value: str) -> dict[str, str]:
    state = (
        "COMPLETED"
        if observed == completed_value
        else "UNKNOWN"
        if observed == "STATUS_UNAVAILABLE"
        else "ACTIVE"
        if observed not in {"NOT_STARTED", "NOT_RECEIVED", "IDLE"}
        else "PENDING"
    )
    return _step(step_id, state, observed)


def _check_step(step_id: str, status: str, result: str) -> dict[str, str]:
    state = "COMPLETED" if status == "PASSED" else "BLOCKED" if status == "FAILED" else "ACTIVE" if status == "RUNNING" else "PENDING"
    return _step(step_id, state, result, result if state == "BLOCKED" else "NONE")


def _step(
    step_id: str,
    state: str,
    detail_code: str,
    error_code: str = "NONE",
) -> dict[str, str]:
    return {
        "id": _code(step_id, "UNKNOWN_STEP"),
        "state": state,
        "detailCode": _code(detail_code, "STATUS_UNAVAILABLE"),
        "errorCode": _code(error_code, "NONE"),
    }


def _enrollment_steps(
    phase: str,
    complete: bool,
) -> tuple[dict[str, str], ...]:
    order = (
        (
            "IDENTITY_PREPARED",
            {
                "CHALLENGE_REQUEST",
                "ENROLLMENT_SUBMISSION",
                "CREDENTIAL_INSTALLATION",
                "K1_CLEANUP",
                "COMPLETE",
            },
        ),
        (
            "CHALLENGE_ACQUIRED",
            {
                "ENROLLMENT_SUBMISSION",
                "CREDENTIAL_INSTALLATION",
                "K1_CLEANUP",
                "COMPLETE",
            },
        ),
        (
            "REQUEST_SUBMITTED",
            {"CREDENTIAL_INSTALLATION", "K1_CLEANUP", "COMPLETE"},
        ),
        ("CREDENTIALS_INSTALLED", {"K1_CLEANUP", "COMPLETE"}),
        ("K1_REMOVED", {"COMPLETE"}),
    )
    return tuple(
        _bool_step(step_id, complete or phase in completed_phases)
        for step_id, completed_phases in order
    )


def _p8_steps(
    phase: str,
    error_code: str,
    event_state: str,
    platform_accepted: bool,
    confirmed: bool,
) -> tuple[dict[str, str], ...]:
    phase_steps = (
        ("DEVICE_ENTRY_URL", "REQUEST_RECEIVED"),
        ("STORE_CHECK", "PERSISTENT_STORE_CHECK"),
        ("CONFIG_CHECK", "CONFIGURATION_CHECK"),
        ("MCU_AND_SENSORS", "MCU_SENSOR_CHECK"),
        ("CAMERA_CAPTURE", "CAMERA_CAPTURE"),
        ("COS_UPLOAD_READBACK", "COS_UPLOAD_READBACK"),
        ("EVIDENCE_RECORDED", "EVIDENCE_PERSISTENCE"),
    )
    failed_phase = {
        "P8_DEVICE_ENTRY_URL_FAILED": "REQUEST_RECEIVED",
        "P8_EXECUTION_FAILED": "REQUEST_RECEIVED",
        "P8_STORAGE_CHECK_FAILED": "PERSISTENT_STORE_CHECK",
        "P8_CONFIGURATION_CHECK_FAILED": "CONFIGURATION_CHECK",
        "P8_MCU_SENSOR_CHECK_FAILED": "MCU_SENSOR_CHECK",
        "P8_CAMERA_CAPTURE_FAILED": "CAMERA_CAPTURE",
        "P8_COS_UPLOAD_READBACK_FAILED": "COS_UPLOAD_READBACK",
        "P8_GRANT_NOT_AVAILABLE": "REQUEST_RECEIVED",
        "P8_EVIDENCE_PERSISTENCE_FAILED": "EVIDENCE_PERSISTENCE",
    }.get(error_code)
    observed_phase = failed_phase if phase == "FAILED" else phase
    phase_indexes = {
        phase_code: index
        for index, (_step_id, phase_code) in enumerate(phase_steps)
    }
    if observed_phase == "EVIDENCE_RECORDED":
        observed_index = len(phase_steps)
    else:
        observed_index = phase_indexes.get(observed_phase, -1)

    steps: list[dict[str, str]] = []
    for index, (step_id, phase_code) in enumerate(phase_steps):
        if confirmed or observed_index > index:
            step = _step(step_id, "COMPLETED", "COMPLETED")
        elif phase == "FAILED" and failed_phase == phase_code:
            step = _step(step_id, "BLOCKED", error_code, error_code)
        elif observed_index == index:
            step = _step(step_id, "ACTIVE", phase_code)
        else:
            step = _step(step_id, "PENDING", "PENDING")
        steps.append(step)

    if event_state != "NOT_RECORDED" and phase != "FAILED":
        steps[-1] = _step("EVIDENCE_RECORDED", "COMPLETED", "COMPLETED")

    if confirmed or platform_accepted:
        transport_step = _step(
            "ONENET_TRANSPORT_ACCEPTED",
            "COMPLETED",
            "COMPLETED",
        )
    elif event_state == "DEAD":
        transport_step = _step(
            "ONENET_TRANSPORT_ACCEPTED",
            "BLOCKED",
            "ACCEPTANCE_EVIDENCE_DELIVERY_DEAD",
            "ACCEPTANCE_EVIDENCE_DELIVERY_DEAD",
        )
    elif event_state != "NOT_RECORDED":
        transport_step = _step(
            "ONENET_TRANSPORT_ACCEPTED",
            "ACTIVE",
            "WAITING_ONENET_ACCEPTANCE",
        )
    else:
        transport_step = _step(
            "ONENET_TRANSPORT_ACCEPTED",
            "PENDING",
            "PENDING",
        )
    steps.append(transport_step)

    if confirmed:
        confirmation_step = _step(
            "PLATFORM_CONFIRMED",
            "COMPLETED",
            "COMPLETED",
        )
    elif platform_accepted:
        confirmation_step = _step(
            "PLATFORM_CONFIRMED",
            "ACTIVE",
            "WAITING_BACKEND_CONFIRMATION",
        )
    else:
        confirmation_step = _step(
            "PLATFORM_CONFIRMED",
            "PENDING",
            "PENDING",
        )
    steps.append(confirmation_step)
    return tuple(steps)


def _runtime_blocking_error(
    service_state: str,
    uart_state: str,
    mqtt_state: str,
    reported_error: str,
) -> str | None:
    if service_state == "FAILED":
        return (
            reported_error
            if reported_error == "RUNTIME_BOOT_FAILED"
            else "RUNTIME_BOOT_FAILED"
        )
    if service_state == "STOPPED":
        return "RUNTIME_SERVICE_STOPPED"
    if uart_state == "FAILED":
        return (
            reported_error
            if reported_error.startswith("UART_")
            else "UART_FAILED"
        )
    if uart_state == "DISCONNECTED":
        return (
            reported_error
            if reported_error.startswith("UART_")
            else "UART_DISCONNECTED"
        )
    if mqtt_state == "FAILED":
        return (
            reported_error
            if reported_error.startswith("MQTT_")
            else "MQTT_FAILED"
        )
    if mqtt_state == "DISCONNECTED" and service_state == "RUNNING":
        return (
            reported_error
            if reported_error.startswith("MQTT_")
            else "MQTT_DISCONNECTED"
        )
    if reported_error != "NONE" and not reported_error.startswith("P8_"):
        return reported_error
    return None


def _runtime_is_fresh(value: dict[str, Any], now: float) -> bool:
    observed = value.get("observedMonotonicMs")
    if not isinstance(observed, int) or isinstance(observed, bool) or observed < 0:
        return False
    age = int(now * 1000) - observed
    return 0 <= age <= 15_000


def _validate_acceptance_projection(value: dict[str, Any]) -> None:
    if set(value) != _P7_FIELDS or type(value.get("schemaVersion")) is not int:
        raise ValueError("P7 projection fields are invalid")
    if value["schemaVersion"] != 1 or value.get("executorAvailable") is not True:
        raise ValueError("P7 projection source is unavailable")
    if value.get("status") not in _P7_STATUSES or not _is_code(
        value.get("phase")
    ):
        raise ValueError("P7 projection state is invalid")
    revision = value.get("revision")
    if type(revision) is not int or revision < 0:
        raise ValueError("P7 projection revision is invalid")
    if value.get("idempotent") is not False:
        raise ValueError("persisted P7 projection cannot be idempotent")
    release_id = value.get("imageReleaseId")
    if release_id is not None and (
        not isinstance(release_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", release_id)
        is None
    ):
        raise ValueError("P7 image release is invalid")
    summary = value.get("hardwareConfigSummary")
    if not isinstance(summary, str) or re.fullmatch(
        r"[0-9A-F]{12}", summary
    ) is None:
        raise ValueError("P7 hardware summary is invalid")
    if value.get("mcuUpdateLineInstalled") not in {None, True, False}:
        raise ValueError("P7 update-line state is invalid")
    if value.get("mcuPeripheralEvidenceMode") not in {
        None,
        "PHYSICAL",
        "SIMULATED_PERIPHERALS",
    }:
        raise ValueError("P7 peripheral evidence mode is invalid")

    identity = value.get("mcuIdentity")
    if identity is not None:
        stable_identity_fields = {
            "fixedFrameRevision",
            "firmwareVersion",
            "firmwareVersionCode",
            "firmwareIdentityHex",
        }
        identity_fields = stable_identity_fields | {
            "mcuBootId",
            "mcuHighestCommandSequence",
            "mcuCapabilityBitmapHex",
        }
        if (
            not isinstance(identity, dict)
            or set(identity) not in (stable_identity_fields, identity_fields)
        ):
            raise ValueError("P7 MCU identity is invalid")
        if any(
            item is not None
            and not isinstance(item, (str, int))
            or isinstance(item, bool)
            for item in identity.values()
        ):
            raise ValueError("P7 MCU identity value is invalid")
        diagnostics = (
            identity.get("mcuBootId"),
            identity.get("mcuHighestCommandSequence"),
            identity.get("mcuCapabilityBitmapHex"),
        )
        if any(item is not None for item in diagnostics):
            if (
                type(diagnostics[0]) is not int
                or not 1 <= diagnostics[0] <= 9_007_199_254_740_991
                or type(diagnostics[1]) is not int
                or not 0 <= diagnostics[1] <= 0xFFFFFFFF
                or not isinstance(diagnostics[2], str)
                or re.fullmatch(r"[0-9a-f]{16}", diagnostics[2]) is None
            ):
                raise ValueError("P7 MCU identity diagnostics are invalid")

    checks = value.get("checks")
    if not isinstance(checks, dict) or set(checks) != _P7_CHECK_NAMES:
        raise ValueError("P7 checks are invalid")
    for check_name, check in checks.items():
        if not isinstance(check, dict):
            raise ValueError("P7 check is invalid")
        fields = set(check)
        if not {"status", "resultCode"}.issubset(fields) or not fields.issubset(
            {"status", "resultCode"} | _P7_CHECK_OPTIONAL_FIELDS
        ):
            raise ValueError("P7 check fields are invalid")
        if not _is_code(check.get("status")) or not _is_code(
            check.get("resultCode")
        ):
            raise ValueError("P7 check state is invalid")
        result = check.get("result")
        if result is not None:
            legacy_result_fields = {
                "preWeightGrams",
                "postWeightGrams",
                "weightDeltaGrams",
                "infraredBlocked",
            }
            result_fields = {
                *legacy_result_fields,
                "fullnessSensorKind",
                "fullnessReadStatus",
                "fullnessDistanceMm",
                "fullnessDistanceThresholdMm",
                "fullnessBlocked",
                "finishReason",
                "deliveryDoorCommand",
                "deliveryDoorOutputStatus",
                "deliveryDoorPhysicalStateBasis",
                "cleanLockPowerState",
                "cleanSolenoidHealth",
                "cleanDoorStateBasis",
                "cleanerPhysicalCloseConfirmed",
            }
            if (
                not isinstance(result, dict)
                or set(result) not in (legacy_result_fields, result_fields)
            ):
                raise ValueError("P7 action result is invalid")
            if set(result) == legacy_result_fields:
                result = result | {
                    name: None for name in result_fields - legacy_result_fields
                }
            for name in (
                "preWeightGrams",
                "postWeightGrams",
                "weightDeltaGrams",
                "fullnessDistanceMm",
                "fullnessDistanceThresholdMm",
            ):
                if result[name] is not None and type(result[name]) is not int:
                    raise ValueError("P7 action measurement is invalid")
            for name in (
                "infraredBlocked",
                "fullnessBlocked",
                "cleanerPhysicalCloseConfirmed",
            ):
                if result[name] is not None and type(result[name]) is not bool:
                    raise ValueError("P7 action flag is invalid")
            allowed_values = {
                "fullnessSensorKind": {None, "DIGITAL_INFRARED", "ULTRASONIC"},
                "fullnessReadStatus": {
                    None,
                    "VALID",
                    "NOT_OBSERVED",
                    "UNAVAILABLE",
                },
                "finishReason": {
                    None,
                    "DELIVERY_END",
                    "DELIVERY_WINDOW_EXPIRED",
                    "CLEAN_CONFIRMED",
                },
                "deliveryDoorCommand": {None, "CLOSE"},
                "deliveryDoorOutputStatus": {None, "COMMAND_DISPATCHED"},
                "deliveryDoorPhysicalStateBasis": {None, "NOT_OBSERVABLE"},
                "cleanLockPowerState": {None, "DEENERGIZED"},
                "cleanSolenoidHealth": {None, "UNKNOWN"},
                "cleanDoorStateBasis": {None, "CLEANER_CONFIRMATION"},
            }
            if any(
                result[name] not in allowed
                for name, allowed in allowed_values.items()
            ):
                raise ValueError("P7 action control fact is invalid")
            kind = result["fullnessSensorKind"]
            if kind is None and any(
                result[name] is not None
                for name in result_fields - legacy_result_fields
            ):
                raise ValueError("P7 legacy action result is invalid")
            if kind == "ULTRASONIC":
                read_status = result["fullnessReadStatus"]
                if read_status == "VALID":
                    if (
                        type(result["fullnessDistanceMm"]) is not int
                        or not 0 <= result["fullnessDistanceMm"] <= 4_000
                        or type(result["fullnessDistanceThresholdMm"]) is not int
                        or not 1
                        <= result["fullnessDistanceThresholdMm"]
                        <= 4_000
                        or type(result["fullnessBlocked"]) is not bool
                        or result["fullnessBlocked"]
                        is not (
                            result["fullnessDistanceMm"]
                            < result["fullnessDistanceThresholdMm"]
                        )
                        or result["infraredBlocked"]
                        is not result["fullnessBlocked"]
                    ):
                        raise ValueError(
                            "P7 ultrasonic fullness fact is invalid"
                        )
                elif read_status in {"NOT_OBSERVED", "UNAVAILABLE"}:
                    if (
                        result["fullnessDistanceMm"] is not None
                        or type(result["fullnessDistanceThresholdMm"]) is not int
                        or not 1
                        <= result["fullnessDistanceThresholdMm"]
                        <= 4_000
                        or result["fullnessBlocked"] is not None
                        or result["infraredBlocked"] is not None
                    ):
                        raise ValueError(
                            "P7 ultrasonic fullness fact is invalid"
                        )
                else:
                    raise ValueError("P7 ultrasonic fullness fact is invalid")
            if kind == "DIGITAL_INFRARED":
                read_status = result["fullnessReadStatus"]
                if read_status == "VALID":
                    if (
                        result["fullnessDistanceMm"] is not None
                        or result["fullnessDistanceThresholdMm"] is not None
                        or type(result["fullnessBlocked"]) is not bool
                        or result["infraredBlocked"]
                        is not result["fullnessBlocked"]
                    ):
                        raise ValueError(
                            "P7 infrared fullness fact is invalid"
                        )
                elif read_status in {"NOT_OBSERVED", "UNAVAILABLE"}:
                    if any(
                        result[name] is not None
                        for name in (
                            "fullnessDistanceMm",
                            "fullnessDistanceThresholdMm",
                            "fullnessBlocked",
                            "infraredBlocked",
                        )
                    ):
                        raise ValueError(
                            "P7 infrared fullness fact is invalid"
                        )
                else:
                    raise ValueError("P7 infrared fullness fact is invalid")
            if check_name == "delivery" and result["finishReason"] is not None:
                if (
                    result["finishReason"]
                    not in {"DELIVERY_END", "DELIVERY_WINDOW_EXPIRED"}
                    or result["deliveryDoorCommand"] != "CLOSE"
                    or result["deliveryDoorOutputStatus"]
                    != "COMMAND_DISPATCHED"
                    or result["deliveryDoorPhysicalStateBasis"]
                    != "NOT_OBSERVABLE"
                    or any(
                        result[name] is not None
                        for name in (
                            "cleanLockPowerState",
                            "cleanSolenoidHealth",
                            "cleanDoorStateBasis",
                            "cleanerPhysicalCloseConfirmed",
                        )
                    )
                ):
                    raise ValueError("P7 delivery control fact is invalid")
            if check_name == "clean" and result["finishReason"] is not None:
                if (
                    result["finishReason"] != "CLEAN_CONFIRMED"
                    or result["cleanLockPowerState"] != "DEENERGIZED"
                    or result["cleanSolenoidHealth"] != "UNKNOWN"
                    or result["cleanDoorStateBasis"]
                    != "CLEANER_CONFIRMATION"
                    or result["cleanerPhysicalCloseConfirmed"] is not True
                    or any(
                        result[name] is not None
                        for name in (
                            "deliveryDoorCommand",
                            "deliveryDoorOutputStatus",
                            "deliveryDoorPhysicalStateBasis",
                        )
                    )
                ):
                    raise ValueError("P7 clean control fact is invalid")
        if check_name == "mcu":
            smoke_code = check.get("selfTestSmokeCode")
            smoke_state = check.get("selfTestSmokeState")
            smoke_health = check.get("selfTestSmokeSensorHealth")
            if (
                "selfTestSmokeState" in check
                or "selfTestSmokeSensorHealth" in check
            ):
                if type(smoke_code) is not int or (
                    smoke_code,
                    smoke_state,
                    smoke_health,
                ) not in {
                    (0, "NORMAL", "OK"),
                    (1, "ALARM", "ALARM"),
                    (2, "UNAVAILABLE", "UNAVAILABLE"),
                    (3, "NOT_OBSERVED", "NOT_OBSERVED"),
                }:
                    raise ValueError("P7 MCU smoke fact is invalid")
            elif "selfTestSmokeCode" in check and (
                type(smoke_code) is not int
                or smoke_code not in {0, 1, 2, 3}
            ):
                raise ValueError("P7 MCU smoke fact is invalid")

            fullness_kind = check.get("selfTestFullnessSensorKind")
            fullness_status = check.get("selfTestFullnessReadStatus")
            current_fullness_fields = {
                "selfTestFullnessSensorKind",
                "selfTestFullnessReadStatus",
                "selfTestFullnessDistanceMm",
                "selfTestFullnessDistanceThresholdMm",
                "selfTestFullnessBlocked",
            }
            if current_fullness_fields & set(check):
                infrared = check.get("selfTestInfraredBlocked")
                distance = check.get("selfTestFullnessDistanceMm")
                threshold = check.get("selfTestFullnessDistanceThresholdMm")
                blocked = check.get("selfTestFullnessBlocked")
                if fullness_kind == "ULTRASONIC":
                    if fullness_status == "VALID":
                        valid_fullness = (
                            type(distance) is int
                            and 0 <= distance <= 4_000
                            and type(threshold) is int
                            and 1 <= threshold <= 4_000
                            and type(blocked) is bool
                            and blocked is (distance < threshold)
                            and infrared is blocked
                        )
                    else:
                        valid_fullness = (
                            fullness_status
                            in {"NOT_OBSERVED", "UNAVAILABLE"}
                            and "selfTestFullnessDistanceMm" not in check
                            and type(threshold) is int
                            and 1 <= threshold <= 4_000
                            and "selfTestFullnessBlocked" not in check
                            and "selfTestInfraredBlocked" not in check
                        )
                elif fullness_kind == "DIGITAL_INFRARED":
                    if fullness_status == "VALID":
                        valid_fullness = (
                            type(infrared) is bool
                            and type(blocked) is bool
                            and blocked is infrared
                            and "selfTestFullnessDistanceMm" not in check
                            and "selfTestFullnessDistanceThresholdMm"
                            not in check
                        )
                    else:
                        valid_fullness = (
                            fullness_status
                            in {"NOT_OBSERVED", "UNAVAILABLE"}
                            and "selfTestInfraredBlocked" not in check
                            and "selfTestFullnessDistanceMm" not in check
                            and "selfTestFullnessDistanceThresholdMm"
                            not in check
                            and "selfTestFullnessBlocked" not in check
                        )
                else:
                    valid_fullness = False
                if not valid_fullness:
                    raise ValueError("P7 MCU fullness fact is invalid")
        for name, item in check.items():
            if name == "sampling":
                if not valid_sampling(item):
                    raise ValueError("P7 weight samples are invalid")
                continue
            if name in {"status", "resultCode", "result"}:
                continue
            if isinstance(item, (dict, list)):
                raise ValueError("P7 check value is invalid")

    recovery = value.get("recovery")
    if recovery is not None:
        if not isinstance(recovery, dict) or set(recovery) != {
            "context",
            "resultCode",
            "hardwareVerified",
            "awaitingDoorConfirmation",
            "awaitingAreaSafetyConfirmation",
        }:
            raise ValueError("P7 recovery is invalid")
        if not _is_code(recovery.get("context")) or not _is_code(
            recovery.get("resultCode")
        ):
            raise ValueError("P7 recovery code is invalid")
        if any(
            not isinstance(recovery.get(name), bool)
            for name in (
                "hardwareVerified",
                "awaitingDoorConfirmation",
                "awaitingAreaSafetyConfirmation",
            )
        ):
            raise ValueError("P7 recovery flags are invalid")

    camera = value.get("cameraReview")
    if camera is not None:
        if not isinstance(camera, dict) or set(camera) != {
            "nonce",
            "expiresMonotonicMs",
            "outsideImage",
            "insideImage",
        }:
            raise ValueError("P7 camera review is invalid")
        if (
            not isinstance(camera.get("nonce"), str)
            or re.fullmatch(r"[0-9a-f]{32}", camera["nonce"]) is None
            or type(camera.get("expiresMonotonicMs")) is not int
            or camera["expiresMonotonicMs"] < 0
            or not isinstance(camera.get("outsideImage"), str)
            or not isinstance(camera.get("insideImage"), str)
        ):
            raise ValueError("P7 camera review value is invalid")

    actions = value.get("allowedActions")
    if (
        not isinstance(actions, list)
        or len(actions) > 16
        or len(set(actions)) != len(actions)
        or any(not _is_code(action) for action in actions)
    ):
        raise ValueError("P7 allowed actions are invalid")


def _validate_cellular_projection(value: dict[str, Any]) -> None:
    try:
        parse_cellular_status(value)
    except ValueError as error:
        raise ValueError("cellular projection is invalid") from error


def _validate_seal_source(value: dict[str, object]) -> _SourceSnapshot:
    candidate = dict(value) if isinstance(value, dict) else {}
    try:
        if set(candidate) != _SEAL_FIELDS:
            raise ValueError("seal projection fields are invalid")
        authorized = candidate.get("authorized")
        confirm_allowed = candidate.get("confirmAllowed")
        code = candidate.get("statusCode")
        generation = candidate.get("acceptanceGeneration")
        binding = candidate.get("authorizationBindingSha256")
        validate_runtime_services(candidate.get("runtimeServices"))
        if (
            not isinstance(authorized, bool)
            or not isinstance(confirm_allowed, bool)
            or code not in _SEAL_STATUS_CODES
            or not (
                generation is None
                or (
                    type(generation) is int
                    and generation > 0
                )
            )
            or not (
                binding is None
                or (
                    isinstance(binding, str)
                    and _HEX_64.fullmatch(binding) is not None
                )
            )
            or (generation is None) != (binding is None)
            or (authorized and generation is None)
            or (
                confirm_allowed
                and (not authorized or code != "SEAL_READY")
            )
            or (
                code
                in {
                    "SEALED_RESPONSE_PENDING",
                    "SEALED_CLEANUP_PENDING",
                    "SEALED",
                }
                and not authorized
            )
        ):
            raise ValueError("seal projection value is invalid")
    except ValueError:
        return _SourceSnapshot(False, {})
    return _SourceSnapshot(
        code != "FACTORY_SEAL_NOT_AVAILABLE",
        candidate,
    )


def _read_acceptance_delivery(path: Path) -> _SourceSnapshot:
    connection: sqlite3.Connection | None = None
    try:
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            return _SourceSnapshot(False, {})
        resolved = path.resolve(strict=True).as_posix()
        uri = f"file:{quote(resolved, safe='/:')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=0.25)
        connection.row_factory = sqlite3.Row
        command = connection.execute(
            """SELECT command_uid, state, last_error
               FROM command_inbox
               WHERE command_type='REQUEST_DEVICE_ACCEPTANCE'
               ORDER BY rowid DESC LIMIT 1"""
        ).fetchone()
        if command is None:
            entry_url_state = _read_device_entry_url_state(connection)
            return _SourceSnapshot(
                True,
                {"deviceEntryUrlState": entry_url_state},
            )
        # Receiving the P8 request proves that the operator already completed
        # the device/bag scan.  QR application evidence is only a prerequisite
        # before that point, so a later damaged QR row must not erase this
        # durable history or hide the otherwise readable command.
        entry_url_state = "PENDING"
        command_uid = command["command_uid"]
        command_state = command["state"]
        command_error = command["last_error"]
        if (
            not isinstance(command_uid, str)
            or not 1 <= len(command_uid) <= 128
            or command_state
            not in {
                "PENDING",
                "PROCESSING",
                "WAITING_MCU_RESULT",
                "RECOVERY_REQUIRED",
                "COMPLETED",
                "FAILED",
                "REJECTED",
                "SUPERSEDED",
            }
            or not (command_error is None or _is_code(command_error))
        ):
            return _SourceSnapshot(False, {})
        event = connection.execute(
            """SELECT state, confirmed_at, platform_accepted_at
               FROM event_outbox
               WHERE event_type='DEVICE_ACCEPTANCE_EVIDENCE'
                 AND json_extract(payload_json, '$.commandUid')=?
               ORDER BY edge_event_sequence DESC LIMIT 1""",
            (command_uid,),
        ).fetchone()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return _SourceSnapshot(False, {})
    finally:
        if connection is not None:
            connection.close()
    result: dict[str, Any] = {
        "deviceEntryUrlState": entry_url_state,
        "command": {
            "state": command_state,
            "lastError": command_error or "NONE",
        }
    }
    if event is not None:
        event_state = event["state"]
        confirmed_at = event["confirmed_at"]
        platform_accepted_at = event["platform_accepted_at"]
        if (
            event_state not in {"PENDING", "SENDING", "CONFIRMED", "DEAD"}
            or not _optional_text(confirmed_at)
            or not _optional_text(platform_accepted_at)
            or (event_state == "CONFIRMED" and confirmed_at is None)
        ):
            return _SourceSnapshot(False, {})
        result["event"] = {
            "state": event_state,
            "confirmed": confirmed_at is not None,
            "platformAccepted": platform_accepted_at is not None,
        }
    return _SourceSnapshot(True, result)


def _read_device_entry_url_state(
    connection: sqlite3.Connection,
) -> str:
    rows = connection.execute(
        """SELECT state_key, state_value FROM device_state
           WHERE state_key IN (
               'device_entry_url',
               'native_device_entry_url_applied_evidence',
               'native_device_entry_url_reload'
           )"""
    ).fetchall()
    values = {row["state_key"]: row["state_value"] for row in rows}
    raw_stored = values.get("device_entry_url")
    if raw_stored is None:
        return "MISSING"
    stored = json.loads(raw_stored)
    if not isinstance(stored, dict) or set(stored) != {
        "deviceEntryUrl",
        "deviceEntryUrlSha256",
        "issuedAt",
    }:
        raise ValueError("stored device entry URL shape is invalid")
    url = stored["deviceEntryUrl"]
    digest = stored["deviceEntryUrlSha256"]
    if (
        not isinstance(url, str)
        or not url.startswith("https://")
        or not 1 <= len(url) <= 192
        or not isinstance(digest, str)
        or _HEX_64.fullmatch(digest) is None
    ):
        raise ValueError("stored device entry URL is invalid")
    try:
        actual_digest = hashlib.sha256(url.encode("ascii")).hexdigest()
    except UnicodeEncodeError as error:
        raise ValueError("stored device entry URL is not ASCII") from error
    if actual_digest != digest:
        raise ValueError("stored device entry URL digest differs")

    raw_evidence = values.get(
        "native_device_entry_url_applied_evidence"
    )
    if raw_evidence is not None:
        evidence = json.loads(raw_evidence)
        if not isinstance(evidence, dict) or set(evidence) != {
            "deviceEntryUrlSha256",
            "applicationUid",
            "mcuCommandUid",
            "mcuBootId",
            "mcuEventSequence",
            "status",
            "faultCode",
            "basis",
        }:
            raise ValueError("device entry URL evidence shape is invalid")
        if (
            evidence["deviceEntryUrlSha256"] == digest
            and type(evidence["mcuBootId"]) is int
            and evidence["mcuBootId"] > 0
            and type(evidence["mcuEventSequence"]) is int
            and evidence["mcuEventSequence"] > 0
            and evidence["status"] == "APPLIED"
            and evidence["faultCode"] is None
            and evidence["basis"]
            == "UART3_COMMAND_ATOMICALLY_QUEUED"
        ):
            return "APPLIED"

    raw_reload = values.get("native_device_entry_url_reload")
    if raw_reload is not None:
        reload = json.loads(raw_reload)
        if not isinstance(reload, dict):
            raise ValueError("device entry URL reload is invalid")
        if (
            reload.get("deviceEntryUrlSha256") == digest
            and reload.get("state") == "FAILED"
        ):
            return "FAILED"
    return "PENDING"


def _read_validated_json(
    path: Path,
    validator: Callable[[dict[str, Any]], None],
    *,
    maximum_bytes: int,
) -> _SourceSnapshot:
    value = _read_json(path, maximum_bytes=maximum_bytes)
    if value is None:
        return _SourceSnapshot(False, {})
    try:
        validator(value)
    except (KeyError, TypeError, ValueError):
        return _SourceSnapshot(False, {})
    return _SourceSnapshot(True, value)


def _read_json(path: Path, *, maximum_bytes: int) -> dict[str, Any] | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or not 1 <= details.st_size <= maximum_bytes:
            return None
        content = os.read(descriptor, maximum_bytes + 1)
    finally:
        os.close(descriptor)
    if len(content) > maximum_bytes:
        return None
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _optional_text(value: object) -> bool:
    return value is None or (
        isinstance(value, str) and 1 <= len(value) <= 128
    )


def _is_code(value: object) -> bool:
    return (
        isinstance(value, str)
        and normalize_error_code(value) == value
        and value != "INTERNAL_ERROR"
        and _SAFE_CODE.fullmatch(value) is not None
    )


def _code(value: object, fallback: str) -> str:
    if isinstance(value, str):
        normalized = normalize_error_code(value)
        if normalized != "INTERNAL_ERROR" and _SAFE_CODE.fullmatch(normalized):
            return normalized
    return fallback


def _require_code(value: object) -> None:
    if not isinstance(value, str) or _SAFE_CODE.fullmatch(value) is None:
        raise ValueError("factory flow code is invalid")


__all__ = [
    "FLOW_NODE_IDS",
    "FactoryFlowPaths",
    "FactoryFlowProjector",
    "empty_factory_flow_projection",
    "validate_factory_flow_projection",
]
