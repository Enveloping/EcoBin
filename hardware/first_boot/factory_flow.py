from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any, Callable
from urllib.parse import quote

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


@dataclass(frozen=True, slots=True)
class FactoryFlowPaths:
    acceptance: Path = Path("/run/ecobin/factory-portal/acceptance.json")
    cellular: Path = Path("/run/ecobin/cellular-uplink/status.json")
    enrollment: Path = Path("/run/ecobin/enrollment/progress.json")
    runtime: Path = Path("/run/ecobin/hardware/factory-progress.json")
    edge_store: Path = Path("/var/lib/ecobin/hardware/edge.db")
    output: Path = Path("/run/ecobin/factory-portal/flow.json")


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
        acceptance = _read_json(
            self._paths.acceptance,
            maximum_bytes=64 * 1024,
        )
        cellular = _read_exact_json(
            self._paths.cellular,
            {"schemaVersion", "resultCode"},
            maximum_bytes=512,
        )
        enrollment = _read_exact_json(
            self._paths.enrollment,
            {"schemaVersion", "phase", "lastErrorCode", "retryable"},
            maximum_bytes=2048,
        )
        runtime = _read_exact_json(
            self._paths.runtime,
            {
                "schemaVersion",
                "serviceState",
                "uartState",
                "mqttState",
                "p8Phase",
                "lastErrorCode",
                "observedMonotonicMs",
            },
            maximum_bytes=4096,
        )
        durable = _read_acceptance_delivery(self._paths.edge_store)
        projection = self._build(
            stage,
            facts,
            error_code=error_code,
            seal=seal,
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
        seal: dict[str, object],
        acceptance: dict[str, Any],
        cellular: dict[str, Any],
        enrollment: dict[str, Any],
        runtime: dict[str, Any],
        durable: dict[str, Any],
    ) -> dict[str, object]:
        nodes: list[dict[str, object]] = []
        public_error = _code(error_code, "NONE")

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

        p7_status = _code(acceptance.get("status"), facts.factory_test_status.value)
        p7_phase = _code(acceptance.get("phase"), "NOT_RUN")
        if p7_status == "PASSED" and facts.factory_report_valid:
            p7_state = "COMPLETED"
        elif p7_status in {"FAILED", "RECOVERY_REQUIRED"}:
            p7_state = "BLOCKED"
        elif p7_status == "RUNNING":
            p7_state = "ACTIVE"
        else:
            p7_state = "WAITING_OPERATOR" if boot_complete else "PENDING"
        p7_steps = []
        checks = acceptance.get("checks")
        checks = checks if isinstance(checks, dict) else {}
        for check_id, source_name in (
            ("MCU", "mcu"),
            ("WEIGHT", "weight"),
            ("MCU_UPDATE_LINE", "upgradeLine"),
            ("CAMERAS", "cameras"),
            ("DELIVERY", "delivery"),
            ("CLEAN", "clean"),
        ):
            check = checks.get(source_name)
            check = check if isinstance(check, dict) else {}
            check_status = _code(check.get("status"), "NOT_RUN")
            check_result = _code(check.get("resultCode"), "NOT_RUN")
            p7_steps.append(_check_step(check_id, check_status, check_result))
        p7_steps.append(_bool_step("REPORT", facts.factory_report_valid))
        nodes.append(_node(
            "LOCAL_HARDWARE_ACCEPTANCE",
            p7_state,
            p7_phase,
            _code(acceptance.get("lastErrorCode"), public_error if p7_state == "BLOCKED" else "NONE"),
            tuple(p7_steps),
        ))

        cellular_result = _code(cellular.get("resultCode"), "STATUS_UNAVAILABLE")
        cellular_complete = facts.uplink_ready and facts.time_trusted
        if not (p7_state == "COMPLETED"):
            cellular_state = "PENDING"
        elif cellular_complete:
            cellular_state = "COMPLETED"
        elif cellular_result in {
            "NONE",
            "STATUS_UNAVAILABLE",
            "TIME_SYNC_PENDING",
            "TIME_SYNC_IN_PROGRESS",
            "FACTORY_TEST_GATE_CLOSED",
        }:
            cellular_state = "ACTIVE"
        else:
            cellular_state = "BLOCKED"
        nodes.append(_node(
            "CELLULAR_AND_TIME",
            cellular_state,
            "CELLULAR_AND_TIME_READY" if cellular_complete else cellular_result,
            cellular_result if cellular_state == "BLOCKED" else "NONE",
            (
                _bool_step("AIR780E_PROFILE", facts.cellular_profile_active),
                _bool_step("CELLULAR_UPLINK", facts.uplink_ready),
                _bool_step("TRUSTED_TIME", facts.time_trusted),
            ),
        ))

        enrollment_phase = _code(enrollment.get("phase"), "NOT_STARTED")
        enrollment_error = _code(enrollment.get("lastErrorCode"), "NONE")
        if not cellular_complete:
            enrollment_state = "PENDING"
        elif facts.enrollment_complete:
            enrollment_state = "COMPLETED"
        elif enrollment_phase == "FAILED" and enrollment.get("retryable") is False:
            enrollment_state = "BLOCKED"
        else:
            enrollment_state = "ACTIVE"
        nodes.append(_node(
            "ENROLLMENT_AND_CREDENTIALS",
            enrollment_state,
            "ENROLLMENT_COMPLETE" if facts.enrollment_complete else enrollment_phase,
            enrollment_error,
            _enrollment_steps(enrollment_phase, facts.enrollment_complete),
        ))

        runtime_fresh = _runtime_is_fresh(runtime, self._monotonic())
        service_state = _code(runtime.get("serviceState"), "STATUS_UNAVAILABLE")
        uart_state = _code(runtime.get("uartState"), "STATUS_UNAVAILABLE")
        mqtt_state = _code(runtime.get("mqttState"), "STATUS_UNAVAILABLE")
        runtime_error = _code(runtime.get("lastErrorCode"), "NONE")
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
        if not facts.enrollment_complete:
            runtime_state = "PENDING"
        elif runtime_complete:
            runtime_state = "COMPLETED"
        elif facts.handoff_safe and (not runtime or not runtime_fresh):
            runtime_state = "UNKNOWN"
        elif runtime_blocking_error is not None:
            runtime_state = "BLOCKED"
        else:
            runtime_state = "ACTIVE"
        runtime_detail = (
            "RUNTIME_READY"
            if runtime_complete
            else "RUNTIME_STATUS_STALE"
            if facts.handoff_safe and (not runtime or not runtime_fresh)
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

        command = durable.get("command")
        command = command if isinstance(command, dict) else {}
        command_state = _code(command.get("state"), "NOT_RECEIVED")
        command_received = bool(command)
        if not runtime_complete:
            bags_state = "PENDING"
        elif command_received:
            bags_state = "COMPLETED"
        else:
            bags_state = "WAITING_OPERATOR"
        nodes.append(_node(
            "FACTORY_BAGS",
            bags_state,
            "P8_REQUEST_RECEIVED" if command_received else "SCAN_DEVICE_AND_FACTORY_BAGS",
            "NONE",
            (_bool_step("P8_REQUEST", command_received),),
        ))

        event = durable.get("event")
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
        p8_phase = _code(runtime.get("p8Phase"), "IDLE")
        command_error = _code(command.get("lastError"), "NONE")
        if not command_received:
            evidence_state = "PENDING"
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
            "EVIDENCE_CONFIRMED"
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
            seal.get("authorized") is True
            and isinstance(seal.get("acceptanceGeneration"), int)
            and not isinstance(seal.get("acceptanceGeneration"), bool)
            and seal.get("acceptanceGeneration", 0) > 0
        )
        seal_code = _code(seal.get("statusCode"), "FACTORY_SEAL_NOT_AVAILABLE")
        if seal_authorized:
            cloud_state = "COMPLETED"
            cloud_detail = "CLOUD_ACCEPTANCE_PASSED"
        elif event_confirmed:
            cloud_state = "ACTIVE"
            cloud_detail = "WAITING_CLOUD_DECISION"
        else:
            cloud_state = "PENDING"
            cloud_detail = "WAITING_ACCEPTANCE_EVIDENCE"
        nodes.append(_node(
            "CLOUD_DECISION_AND_AUTHORIZATION",
            cloud_state,
            cloud_detail,
            "NONE",
            (
                _bool_step("EVIDENCE_CONFIRMED", event_confirmed),
                _bool_step("CURRENT_GENERATION_AUTHORIZED", seal_authorized),
            ),
        ))

        sealing = seal_code in {
            "SEALED_RESPONSE_PENDING",
            "SEALED_CLEANUP_PENDING",
            "SEALED",
        }
        confirm_allowed = seal.get("confirmAllowed") is True
        if sealing:
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


def _state_step(step_id: str, observed: str, completed_value: str) -> dict[str, str]:
    state = "COMPLETED" if observed == completed_value else "ACTIVE" if observed not in {"NOT_STARTED", "NOT_RECEIVED", "STATUS_UNAVAILABLE", "IDLE"} else "PENDING"
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
        "P8_GRANT_NOT_AVAILABLE": "COS_UPLOAD_READBACK",
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


def _read_acceptance_delivery(path: Path) -> dict[str, Any]:
    try:
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            return {}
        resolved = path.resolve(strict=True).as_posix()
        uri = f"file:{quote(resolved, safe='/:')}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.25) as connection:
            connection.row_factory = sqlite3.Row
            command = connection.execute(
                """SELECT command_uid, state, last_error
                   FROM command_inbox
                   WHERE command_type='REQUEST_DEVICE_ACCEPTANCE'
                   ORDER BY rowid DESC LIMIT 1"""
            ).fetchone()
            if command is None:
                return {}
            event = connection.execute(
                """SELECT state, confirmed_at, platform_accepted_at
                   FROM event_outbox
                   WHERE event_type='DEVICE_ACCEPTANCE_EVIDENCE'
                     AND json_extract(payload_json, '$.commandUid')=?
                   ORDER BY edge_event_sequence DESC LIMIT 1""",
                (command["command_uid"],),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return {}
    result: dict[str, Any] = {
        "command": {
            "state": _code(command["state"], "UNKNOWN"),
            "lastError": _code(command["last_error"], "NONE"),
        }
    }
    if event is not None:
        result["event"] = {
            "state": _code(event["state"], "UNKNOWN"),
            "confirmed": event["confirmed_at"] is not None,
            "platformAccepted": event["platform_accepted_at"] is not None,
        }
    return result


def _read_exact_json(
    path: Path,
    exact_fields: set[str],
    *,
    maximum_bytes: int,
) -> dict[str, Any]:
    value = _read_json(path, maximum_bytes=maximum_bytes)
    if set(value) != exact_fields or value.get("schemaVersion") != 1:
        return {}
    return value


def _read_json(path: Path, *, maximum_bytes: int) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return {}
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or not 1 <= details.st_size <= maximum_bytes:
            return {}
        content = os.read(descriptor, maximum_bytes + 1)
    finally:
        os.close(descriptor)
    if len(content) > maximum_bytes:
        return {}
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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
