from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from first_boot.factory_flow import (
    FactoryFlowPaths,
    FactoryFlowProjector,
    validate_factory_flow_projection,
)
from first_boot.model import FactoryTestStatus, FirstBootFacts, FirstBootStage
from factory_seal.runtime_health import unknown_runtime_services


COMMAND_UID = "12345678-1234-4123-8123-123456789abc"


def _paths(tmp_path: Path) -> FactoryFlowPaths:
    return FactoryFlowPaths(
        acceptance=tmp_path / "acceptance.json",
        cellular=tmp_path / "cellular.json",
        enrollment=tmp_path / "enrollment.json",
        runtime=tmp_path / "runtime.json",
        edge_store=tmp_path / "edge.db",
        output=tmp_path / "run" / "flow.json",
    )


def _write(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _seal(
    *,
    authorized: bool = False,
    confirm_allowed: bool = False,
    status_code: str = "CLOUD_ACCEPTANCE_REQUIRED",
) -> dict[str, object]:
    return {
        "authorized": authorized,
        "confirmAllowed": confirm_allowed,
        "statusCode": status_code,
        "acceptanceGeneration": 1 if authorized else None,
        "authorizationBindingSha256": "a" * 64 if authorized else None,
        "runtimeServices": unknown_runtime_services(),
    }


def _passed_facts(**overrides: object) -> FirstBootFacts:
    values: dict[str, object] = {
        "system_prepared": True,
        "factory_portal_ready": True,
        "factory_test_status": FactoryTestStatus.PASSED,
        "factory_report_valid": True,
        "cellular_profile_active": True,
        "uplink_ready": True,
        "time_trusted": True,
        "enrollment_complete": True,
        "handoff_safe": True,
    }
    values.update(overrides)
    return FirstBootFacts(**values)


def _p7_projection(
    *,
    status: str = "PASSED",
    phase: str = "COMPLETE",
) -> dict[str, object]:
    check_status = "PASSED" if status == "PASSED" else "NOT_RUN"
    return {
        "schemaVersion": 1,
        "executorAvailable": True,
        "status": status,
        "phase": phase,
        "revision": 1,
        "idempotent": False,
        "imageReleaseId": None,
        "hardwareConfigSummary": "A" * 12,
        "mcuIdentity": None,
        "mcuUpdateLineInstalled": None,
        "mcuPeripheralEvidenceMode": None,
        "checks": {
            name: {"status": check_status, "resultCode": check_status}
            for name in (
                "mcu",
                "weight",
                "upgradeLine",
                "cameras",
                "delivery",
                "clean",
            )
        },
        "recovery": None,
        "cameraReview": None,
        "allowedActions": ["START"] if status == "NOT_RUN" else [],
    }


def _current_p7_projection_with_unavailable_ultrasonic() -> dict[str, object]:
    acceptance = _p7_projection()
    acceptance["checks"]["mcu"].update(
        {
            "selfTestSmokeCode": 0,
            "selfTestSmokeState": "NORMAL",
            "selfTestSmokeSensorHealth": "OK",
            "selfTestFullnessSensorKind": "ULTRASONIC",
            "selfTestFullnessReadStatus": "UNAVAILABLE",
            "selfTestFullnessDistanceThresholdMm": 600,
            "selfTestWeightGrams": 28,
        }
    )
    acceptance["checks"]["delivery"]["result"] = {
        "preWeightGrams": 28,
        "postWeightGrams": 30,
        "weightDeltaGrams": 2,
        "infraredBlocked": None,
        "fullnessSensorKind": "ULTRASONIC",
        "fullnessReadStatus": "UNAVAILABLE",
        "fullnessDistanceMm": None,
        "fullnessDistanceThresholdMm": 600,
        "fullnessBlocked": None,
        "finishReason": "DELIVERY_WINDOW_EXPIRED",
        "deliveryDoorCommand": "CLOSE",
        "deliveryDoorOutputStatus": "COMMAND_DISPATCHED",
        "deliveryDoorPhysicalStateBasis": "NOT_OBSERVABLE",
        "cleanLockPowerState": None,
        "cleanSolenoidHealth": None,
        "cleanDoorStateBasis": None,
        "cleanerPhysicalCloseConfirmed": None,
    }
    return acceptance


def _legacy_delivery_result(*, expanded: bool) -> dict[str, object]:
    result: dict[str, object] = {
        "preWeightGrams": 28,
        "postWeightGrams": 30,
        "weightDeltaGrams": 2,
        "infraredBlocked": None,
    }
    if expanded:
        result.update(
            {
                "fullnessSensorKind": None,
                "fullnessReadStatus": None,
                "fullnessDistanceMm": None,
                "fullnessDistanceThresholdMm": None,
                "fullnessBlocked": None,
                "finishReason": None,
                "deliveryDoorCommand": None,
                "deliveryDoorOutputStatus": None,
                "deliveryDoorPhysicalStateBasis": None,
                "cleanLockPowerState": None,
                "cleanSolenoidHealth": None,
                "cleanDoorStateBasis": None,
                "cleanerPhysicalCloseConfirmed": None,
            }
        )
    return result


def _create_empty_delivery_store(paths: FactoryFlowPaths) -> None:
    with sqlite3.connect(paths.edge_store) as connection:
        connection.executescript(
            """
            CREATE TABLE device_state (
                state_key TEXT PRIMARY KEY,
                state_value TEXT,
                updated_at TEXT
            );
            CREATE TABLE command_inbox (
                command_uid TEXT,
                command_type TEXT,
                state TEXT,
                last_error TEXT
            );
            CREATE TABLE event_outbox (
                event_type TEXT,
                payload_json TEXT,
                state TEXT,
                confirmed_at TEXT,
                platform_accepted_at TEXT,
                edge_event_sequence INTEGER
            );
            CREATE TABLE confirmation_inbox (
                confirmation_uid TEXT PRIMARY KEY,
                event_uid TEXT,
                outcome TEXT,
                payload_json TEXT
            );
            """
        )


def _insert_current_device_entry_url_proof(paths: FactoryFlowPaths) -> None:
    url = "https://www.jinshoubao.com/d/factory"
    digest = hashlib.sha256(url.encode("ascii")).hexdigest()
    stored = {
        "deviceEntryUrl": url,
        "deviceEntryUrlSha256": digest,
        "issuedAt": "2026-09-15T00:00:00.000Z",
    }
    evidence = {
        "deviceEntryUrlSha256": digest,
        "applicationUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "mcuCommandUid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "mcuBootId": 1,
        "mcuEventSequence": 1,
        "status": "APPLIED",
        "faultCode": None,
        "basis": "UART3_COMMAND_ATOMICALLY_QUEUED",
    }
    with sqlite3.connect(paths.edge_store) as connection:
        connection.executemany(
            "INSERT INTO device_state VALUES (?, ?, '2026-09-15T00:00:00Z')",
            (
                ("device_entry_url", json.dumps(stored)),
                (
                    "native_device_entry_url_applied_evidence",
                    json.dumps(evidence),
                ),
            ),
        )


def _write_completed_sources(paths: FactoryFlowPaths) -> None:
    _write(paths.acceptance, _p7_projection())
    _write(
        paths.cellular,
        {
            "schemaVersion": 2,
            "resultCode": "NONE",
            "consecutiveFailureCount": 0,
            "nextRetryAtMonotonicMs": None,
        },
    )
    _write(
        paths.enrollment,
        {
            "schemaVersion": 1,
            "phase": "COMPLETE",
            "lastErrorCode": None,
            "retryable": False,
        },
    )
    _write(
        paths.runtime,
        {
            "schemaVersion": 1,
            "serviceState": "RUNNING",
            "uartState": "READY",
            "mqttState": "CONNECTED",
            "p8Phase": "EVIDENCE_RECORDED",
            "lastErrorCode": None,
            "observedMonotonicMs": 100_000,
        },
    )
    _create_empty_delivery_store(paths)
    with sqlite3.connect(paths.edge_store) as connection:
        connection.execute(
            "INSERT INTO command_inbox VALUES (?, 'REQUEST_DEVICE_ACCEPTANCE', 'COMPLETED', NULL)",
            (COMMAND_UID,),
        )
        connection.execute(
            """INSERT INTO event_outbox
               VALUES ('DEVICE_ACCEPTANCE_EVIDENCE', ?, 'CONFIRMED',
                       '2026-08-31T12:00:00Z', '2026-08-31T12:00:01Z', 1)""",
            (json.dumps({"commandUid": COMMAND_UID}),),
        )


def test_projection_starts_with_the_first_operator_step(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write(
        paths.acceptance,
        _p7_projection(status="NOT_RUN", phase="NOT_RUN"),
    )
    projector = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    )

    projection = projector.publish(
        FirstBootStage.FACTORY_TEST_REQUIRED,
        FirstBootFacts(system_prepared=True, factory_portal_ready=True),
        error_code="NONE",
        seal=_seal(),
    )

    assert projection["currentNode"] == "LOCAL_HARDWARE_ACCEPTANCE"
    assert projection["overallState"] == "WAITING_OPERATOR"
    assert [node["id"] for node in projection["nodes"]] == [
        "BOOT_AND_PORTAL",
        "LOCAL_HARDWARE_ACCEPTANCE",
        "CELLULAR_AND_TIME",
        "ENROLLMENT_AND_CREDENTIALS",
        "RUNTIME_AND_MQTT",
        "FACTORY_BAGS",
        "CLOUD_EVIDENCE",
        "CLOUD_DECISION_AND_AUTHORIZATION",
        "FACTORY_SEAL",
    ]
    assert validate_factory_flow_projection(projection) == projection
    assert json.loads(paths.output.read_text(encoding="utf-8")) == projection


def test_missing_p7_source_is_unknown_once_local_acceptance_is_reached(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.FACTORY_TEST_REQUIRED,
        FirstBootFacts(system_prepared=True, factory_portal_ready=True),
        error_code="NONE",
        seal=_seal(),
    )

    assert projection["nodes"][1]["state"] == "UNKNOWN"
    assert projection["nodes"][1]["detailCode"] == "STATUS_UNAVAILABLE"


def test_invalid_p7_source_is_unknown_instead_of_running(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write(
        paths.acceptance,
        {"schemaVersion": 1, "status": "RUNNING", "phase": "CHECK_MCU"},
    )

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.FACTORY_TEST_RUNNING,
        FirstBootFacts(system_prepared=True, factory_portal_ready=True),
        error_code="NONE",
        seal=_seal(),
    )

    assert projection["nodes"][1]["state"] == "UNKNOWN"


def test_passed_p7_accepts_nonblocking_unavailable_ultrasonic_fact(
    tmp_path: Path,
) -> None:
    """An optional failed reading must not hide an otherwise valid P7 pass."""

    paths = _paths(tmp_path)
    acceptance = _current_p7_projection_with_unavailable_ultrasonic()
    _write(paths.acceptance, acceptance)

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    local_acceptance = projection["nodes"][1]
    assert local_acceptance["state"] == "COMPLETED"
    assert {step["id"]: step["state"] for step in local_acceptance["steps"]}[
        "DELIVERY"
    ] == "COMPLETED"


@pytest.mark.parametrize(
    "updates",
    (
        {
            "selfTestSmokeCode": 0,
            "selfTestSmokeState": "ALARM",
            "selfTestSmokeSensorHealth": "OK",
        },
        {
            "selfTestSmokeCode": True,
            "selfTestSmokeState": "ALARM",
            "selfTestSmokeSensorHealth": "ALARM",
        },
        {
            "selfTestFullnessSensorKind": "ULTRASONIC",
            "selfTestFullnessReadStatus": "UNAVAILABLE",
            "selfTestFullnessDistanceThresholdMm": -999,
        },
        {
            "selfTestFullnessSensorKind": "ULTRASONIC",
            "selfTestFullnessReadStatus": "UNAVAILABLE",
            "selfTestFullnessDistanceMm": 100,
            "selfTestFullnessDistanceThresholdMm": 600,
        },
    ),
)
def test_malformed_current_p7_mcu_facts_remain_unavailable(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    paths = _paths(tmp_path)
    acceptance = _p7_projection()
    acceptance["checks"]["mcu"].update(updates)
    _write(paths.acceptance, acceptance)

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    assert projection["nodes"][1]["state"] == "UNKNOWN"


def test_passed_p7_accepts_not_observed_smoke_fact(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    acceptance = _p7_projection()
    acceptance["checks"]["mcu"].update(
        {
            "selfTestSmokeCode": 3,
            "selfTestSmokeState": "NOT_OBSERVED",
            "selfTestSmokeSensorHealth": "NOT_OBSERVED",
        }
    )
    _write(paths.acceptance, acceptance)

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    assert projection["nodes"][1]["state"] == "COMPLETED"


@pytest.mark.parametrize(
    "updates",
    (
        {
            "fullnessDistanceThresholdMm": -999,
        },
        {
            "fullnessReadStatus": "VALID",
            "fullnessDistanceMm": 5_000,
            "fullnessDistanceThresholdMm": 600,
            "fullnessBlocked": False,
            "infraredBlocked": False,
        },
    ),
)
def test_malformed_p7_action_fullness_fact_remains_unavailable(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    paths = _paths(tmp_path)
    acceptance = _current_p7_projection_with_unavailable_ultrasonic()
    acceptance["checks"]["delivery"]["result"].update(updates)
    _write(paths.acceptance, acceptance)

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    assert projection["nodes"][1]["state"] == "UNKNOWN"


@pytest.mark.parametrize("expanded", (False, True))
def test_p7_accepts_raw_and_expanded_legacy_action_result(
    tmp_path: Path,
    expanded: bool,
) -> None:
    paths = _paths(tmp_path)
    acceptance = _p7_projection()
    acceptance["checks"]["delivery"]["result"] = _legacy_delivery_result(
        expanded=expanded
    )
    _write(paths.acceptance, acceptance)

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    assert projection["nodes"][1]["state"] == "COMPLETED"


def test_current_p7_action_cannot_disguise_fullness_fact_as_legacy_result(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    acceptance = _p7_projection()
    acceptance["checks"]["delivery"]["result"] = _legacy_delivery_result(
        expanded=True
    )
    acceptance["checks"]["delivery"]["result"]["fullnessReadStatus"] = (
        "VALID"
    )
    _write(paths.acceptance, acceptance)

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    assert projection["nodes"][1]["state"] == "UNKNOWN"


def test_projection_reaches_cloud_pass_and_waits_for_local_seal(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)
    projector = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    )

    projection = projector.publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(
            authorized=True,
            confirm_allowed=True,
            status_code="SEAL_READY",
        ),
    )

    nodes = {node["id"]: node for node in projection["nodes"]}
    assert nodes["FACTORY_BAGS"]["state"] == "COMPLETED"
    assert nodes["CLOUD_EVIDENCE"]["state"] == "COMPLETED"
    assert nodes["CLOUD_DECISION_AND_AUTHORIZATION"]["state"] == "COMPLETED"
    assert nodes["FACTORY_SEAL"]["state"] == "WAITING_OPERATOR"
    assert projection["currentNode"] == "FACTORY_SEAL"

    sealed = projector.publish(
        FirstBootStage.SEALED,
        _passed_facts(sealed_exists=True, sealed_valid=True),
        error_code="NONE",
        seal=_seal(
            authorized=True,
            status_code="SEALED_RESPONSE_PENDING",
        ),
    )
    assert sealed["overallState"] == "COMPLETED"
    assert all(node["state"] == "COMPLETED" for node in sealed["nodes"])


def test_backend_failed_acceptance_blocks_instead_of_waiting_for_seal(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)
    with sqlite3.connect(paths.edge_store) as connection:
        event_uid = "30000000-0000-4000-8000-000000000001"
        connection.execute(
            "UPDATE event_outbox SET payload_json=?",
            (json.dumps({
                "commandUid": COMMAND_UID,
                "eventUid": event_uid,
            }),),
        )
        connection.execute(
            "INSERT INTO confirmation_inbox VALUES (?, ?, ?, ?)",
            (
                "60000000-0000-4000-8000-000000000001",
                event_uid,
                "BUSINESS_APPLIED",
                json.dumps({
                    "resultReferences": [
                        {"type": "DEVICE_ACCEPTANCE", "key": "FAILED"}
                    ]
                }),
            ),
        )

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    cloud = {
        node["id"]: node for node in projection["nodes"]
    }["CLOUD_DECISION_AND_AUTHORIZATION"]
    assert cloud["state"] == "BLOCKED"
    assert cloud["detailCode"] == "CLOUD_ACCEPTANCE_FAILED"
    assert cloud["errorCode"] == "CLOUD_ACCEPTANCE_FAILED"


def test_onenet_transport_acceptance_does_not_confirm_backend_business(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)
    with sqlite3.connect(paths.edge_store) as connection:
        connection.execute(
            """UPDATE event_outbox
               SET state='PENDING', confirmed_at=NULL,
                   platform_accepted_at='2026-08-31T12:00:01Z'"""
        )
    projector = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    )

    projection = projector.publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    nodes = {node["id"]: node for node in projection["nodes"]}
    evidence = nodes["CLOUD_EVIDENCE"]
    evidence_steps = {step["id"]: step for step in evidence["steps"]}
    assert evidence["state"] == "ACTIVE"
    assert evidence["detailCode"] == "WAITING_BACKEND_CONFIRMATION"
    assert evidence_steps["ONENET_TRANSPORT_ACCEPTED"]["state"] == "COMPLETED"
    assert evidence_steps["PLATFORM_CONFIRMED"]["state"] == "ACTIVE"
    assert nodes["CLOUD_DECISION_AND_AUTHORIZATION"]["state"] == "PENDING"


def test_missing_runtime_heartbeat_is_unknown_after_uart_handoff(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write(paths.acceptance, _p7_projection())
    _write(paths.cellular, {"schemaVersion": 1, "resultCode": "NONE"})
    _write(
        paths.enrollment,
        {
            "schemaVersion": 1,
            "phase": "COMPLETE",
            "lastErrorCode": None,
            "retryable": False,
        },
    )
    projector = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    )

    projection = projector.publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    runtime = projection["nodes"][4]
    assert runtime["id"] == "RUNTIME_AND_MQTT"
    assert runtime["state"] == "UNKNOWN"
    assert runtime["detailCode"] == "STATUS_UNAVAILABLE"


@pytest.mark.parametrize(
    ("source", "node_index"),
    (
        ("cellular", 2),
        ("enrollment", 3),
        ("runtime", 4),
    ),
)
def test_invalid_reached_progress_source_is_unknown(
    tmp_path: Path,
    source: str,
    node_index: int,
) -> None:
    paths = _paths(tmp_path)
    _write(paths.acceptance, _p7_projection())
    _write(paths.cellular, {"schemaVersion": 1, "resultCode": "NONE"})
    _write(
        paths.enrollment,
        {
            "schemaVersion": 1,
            "phase": "COMPLETE",
            "lastErrorCode": None,
            "retryable": False,
        },
    )
    _write(
        paths.runtime,
        {
            "schemaVersion": 1,
            "serviceState": "RUNNING",
            "uartState": "READY",
            "mqttState": "CONNECTED",
            "p8Phase": "IDLE",
            "lastErrorCode": None,
            "observedMonotonicMs": 100_000,
        },
    )
    invalid_documents = {
        "cellular": {"schemaVersion": 1, "resultCode": "not-stable"},
        "enrollment": {
            "schemaVersion": 1,
            "phase": "MADE_UP_PHASE",
            "lastErrorCode": None,
            "retryable": False,
        },
        "runtime": {
            "schemaVersion": 1,
            "serviceState": "MAGIC",
            "uartState": "READY",
            "mqttState": "CONNECTED",
            "p8Phase": "IDLE",
            "lastErrorCode": None,
            "observedMonotonicMs": 100_000,
        },
    }
    _write(getattr(paths, source), invalid_documents[source])

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    node = projection["nodes"][node_index]
    assert node["state"] == "UNKNOWN"
    assert node["detailCode"] == "STATUS_UNAVAILABLE"


def test_healthy_store_without_p8_request_waits_for_factory_scan(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)
    with sqlite3.connect(paths.edge_store) as connection:
        connection.execute("DELETE FROM event_outbox")
        connection.execute("DELETE FROM command_inbox")
    _insert_current_device_entry_url_proof(paths)

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    bags = projection["nodes"][5]
    assert bags["state"] == "WAITING_OPERATOR"
    assert bags["detailCode"] == "SCAN_DEVICE_AND_FACTORY_BAGS"


def test_factory_scan_waits_for_the_enrolled_qr_apply_result(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)
    with sqlite3.connect(paths.edge_store) as connection:
        connection.execute("DELETE FROM event_outbox")
        connection.execute("DELETE FROM command_inbox")

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    bags = projection["nodes"][5]
    assert bags["state"] == "ACTIVE"
    assert bags["detailCode"] == "PREPARING_DEVICE_ENTRY_QR"
    assert bags["errorCode"] == "NONE"
    assert bags["steps"][0] == {
        "id": "DEVICE_ENTRY_QR",
        "state": "PENDING",
        "detailCode": "PENDING",
        "errorCode": "NONE",
    }


def test_received_p8_request_does_not_regress_when_qr_proof_is_missing(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    bags = projection["nodes"][5]
    assert bags["state"] == "COMPLETED"
    assert bags["detailCode"] == "P8_REQUEST_RECEIVED"
    assert bags["steps"][0]["state"] == "COMPLETED"
    assert bags["steps"][1]["state"] == "COMPLETED"


def test_received_p8_request_does_not_regress_when_qr_proof_is_corrupt(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)
    _insert_current_device_entry_url_proof(paths)
    with sqlite3.connect(paths.edge_store) as connection:
        connection.execute(
            """UPDATE device_state SET state_value='{'
               WHERE state_key='native_device_entry_url_applied_evidence'"""
        )

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    bags = projection["nodes"][5]
    assert bags["state"] == "COMPLETED"
    assert bags["detailCode"] == "P8_REQUEST_RECEIVED"
    assert bags["steps"][0]["state"] == "COMPLETED"
    assert bags["steps"][1]["state"] == "COMPLETED"


def test_unavailable_store_is_unknown_instead_of_waiting_for_scan(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)
    paths = replace(paths, edge_store=tmp_path / "missing-edge.db")

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    bags = projection["nodes"][5]
    assert bags["state"] == "UNKNOWN"
    assert bags["detailCode"] == "STATUS_UNAVAILABLE"


def test_unavailable_seal_source_is_unknown_after_evidence_confirmation(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(status_code="FACTORY_SEAL_NOT_AVAILABLE"),
    )

    cloud = projection["nodes"][7]
    assert cloud["state"] == "UNKNOWN"
    assert cloud["detailCode"] == "STATUS_UNAVAILABLE"
    assert projection["nodes"][8]["state"] == "PENDING"


@pytest.mark.parametrize(
    ("updates", "expected_error"),
    (
        ({"serviceState": "STOPPED"}, "RUNTIME_SERVICE_STOPPED"),
        ({"serviceState": "FAILED"}, "RUNTIME_BOOT_FAILED"),
        ({"uartState": "DISCONNECTED"}, "UART_DISCONNECTED"),
        ({"uartState": "FAILED"}, "UART_FAILED"),
        ({"mqttState": "FAILED"}, "MQTT_FAILED"),
        ({"mqttState": "DISCONNECTED"}, "MQTT_DISCONNECTED"),
    ),
)
def test_fresh_terminal_runtime_states_are_blocked_with_stable_errors(
    tmp_path: Path,
    updates: dict[str, str],
    expected_error: str,
) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)
    runtime = json.loads(paths.runtime.read_text(encoding="utf-8"))
    runtime.update(updates)
    _write(paths.runtime, runtime)
    projector = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    )

    projection = projector.publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    runtime_node = projection["nodes"][4]
    assert runtime_node["state"] == "BLOCKED"
    assert runtime_node["detailCode"] == expected_error
    assert runtime_node["errorCode"] == expected_error


def test_mqtt_disconnected_is_transitional_while_runtime_is_starting(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)
    runtime = json.loads(paths.runtime.read_text(encoding="utf-8"))
    runtime.update({
        "serviceState": "STARTING",
        "mqttState": "DISCONNECTED",
    })
    _write(paths.runtime, runtime)
    projector = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    )

    projection = projector.publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    runtime_node = projection["nodes"][4]
    assert runtime_node["state"] == "ACTIVE"
    assert runtime_node["errorCode"] == "NONE"


def test_p8_steps_mark_the_current_phase_and_retain_the_failed_phase(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)
    with sqlite3.connect(paths.edge_store) as connection:
        connection.execute("DELETE FROM event_outbox")
    runtime = json.loads(paths.runtime.read_text(encoding="utf-8"))
    runtime["p8Phase"] = "CAMERA_CAPTURE"
    _write(paths.runtime, runtime)
    projector = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    )

    active = projector.publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )
    active_steps = {
        step["id"]: step
        for step in active["nodes"][6]["steps"]
    }
    assert active_steps["MCU_AND_SENSORS"]["state"] == "COMPLETED"
    assert active_steps["CAMERA_CAPTURE"]["state"] == "ACTIVE"
    assert active_steps["COS_UPLOAD_READBACK"]["state"] == "PENDING"

    runtime["p8Phase"] = "FAILED"
    runtime["lastErrorCode"] = "P8_CAMERA_CAPTURE_FAILED"
    _write(paths.runtime, runtime)
    failed = projector.publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )
    failed_evidence = failed["nodes"][6]
    failed_steps = {
        step["id"]: step
        for step in failed_evidence["steps"]
    }
    assert failed_evidence["state"] == "BLOCKED"
    assert failed_steps["MCU_AND_SENSORS"]["state"] == "COMPLETED"
    assert failed_steps["CAMERA_CAPTURE"] == {
        "id": "CAMERA_CAPTURE",
        "state": "BLOCKED",
        "detailCode": "P8_CAMERA_CAPTURE_FAILED",
        "errorCode": "P8_CAMERA_CAPTURE_FAILED",
    }
    assert failed_steps["COS_UPLOAD_READBACK"]["state"] == "PENDING"


def test_p8_failure_surfaces_the_allowlisted_device_error(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)
    runtime = json.loads(paths.runtime.read_text(encoding="utf-8"))
    runtime["p8Phase"] = "FAILED"
    runtime["lastErrorCode"] = "P8_CAMERA_CAPTURE_FAILED"
    _write(paths.runtime, runtime)
    projector = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    )

    projection = projector.publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    evidence = projection["nodes"][6]
    assert evidence["state"] == "BLOCKED"
    assert evidence["errorCode"] == "P8_CAMERA_CAPTURE_FAILED"


def test_missing_p8_grant_stays_at_the_request_stage(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_completed_sources(paths)
    with sqlite3.connect(paths.edge_store) as connection:
        connection.execute("DELETE FROM event_outbox")
    runtime = json.loads(paths.runtime.read_text(encoding="utf-8"))
    runtime["p8Phase"] = "FAILED"
    runtime["lastErrorCode"] = "P8_GRANT_NOT_AVAILABLE"
    _write(paths.runtime, runtime)

    projection = FactoryFlowProjector(
        paths,
        owner=lambda _path: None,
        monotonic=lambda: 100.0,
    ).publish(
        FirstBootStage.ENROLLMENT_COMPLETE,
        _passed_facts(),
        error_code="NONE",
        seal=_seal(),
    )

    steps = {
        step["id"]: step
        for step in projection["nodes"][6]["steps"]
    }
    assert steps["DEVICE_ENTRY_URL"]["state"] == "BLOCKED"
    assert steps["DEVICE_ENTRY_URL"]["errorCode"] == "P8_GRANT_NOT_AVAILABLE"
    assert steps["STORE_CHECK"]["state"] == "PENDING"
    assert steps["CONFIG_CHECK"]["state"] == "PENDING"
    assert steps["MCU_AND_SENSORS"]["state"] == "PENDING"
    assert steps["CAMERA_CAPTURE"]["state"] == "PENDING"
    assert steps["COS_UPLOAD_READBACK"]["state"] == "PENDING"
