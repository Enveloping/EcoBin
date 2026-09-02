"""Stage-four regressions for delivery terminal and fact binding."""

import copy

import pytest

from command_processor import CommandProcessor
from job_safety import JobSafetyError
from tests.test_command_processor import (
    FakePhotoManager,
    FakeUart,
    make_real_job_safety,
    make_store,
    valid_service_command,
)
from work_manager import WorkManager


@pytest.mark.parametrize(
    "output_status,fault_code",
    [
        ("OUTPUT_REJECTED", "INTERLOCK_ACTIVE"),
        ("COMMAND_SUPERSEDED_BEFORE_DISPATCH", "NONE"),
    ],
)
def test_delivery_open_not_executed_fails_job_and_releases_permit(
    tmp_path,
    output_status,
    fault_code,
):
    """An explicit safe OPEN failure must never expose a delivery window."""

    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    updater, safety = make_real_job_safety(tmp_path)
    uart = FakeUart()
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    )

    assert processor.process_next() is True
    session_uid = command["payload"]["sessionUid"]
    port_no = command["payload"]["portNo"]
    start_uid = uart.calls[-1][2]
    processor.process_mcu_event(
        {
            "message_name": "WORK_PREOPEN_WEIGHT_READY",
            "message_type": 48,
            "source_tx_sequence": 7,
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 1,
                "uptimeMs": 1_000,
                "mcuCommandUid": start_uid,
                "sessionUid": session_uid,
                "portNo": port_no,
                "roundIndex": 1,
                "measurementUid": (
                    "52000000-0000-4000-8000-000000000201"
                ),
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 1_000,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        }
    )
    authorize_uid = uart.calls[-1][2]
    assert uart.calls[-1][0] == "AUTHORIZE_DELIVERY_FIRST_OPEN"
    assert updater.get_status()["activeJobPermitCount"] == 1

    processor.process_mcu_event(
        {
            "message_name": "DELIVERY_DOOR_COMMAND_RESULT",
            "message_type": 49,
            "source_tx_sequence": 8,
            "payload": {
                "mcuBootId": 42,
                "mcuEventSequence": 2,
                "uptimeMs": 2_000,
                "mcuCommandUid": authorize_uid,
                "sessionUid": session_uid,
                "portNo": port_no,
                "roundIndex": 1,
                "command": "OPEN",
                "outputStatus": output_status,
                "physicalDoorStateBasis": "NOT_OBSERVABLE",
                "faultCode": fault_code,
            },
        }
    )

    action = updater.get_physical_action({"actionUid": authorize_uid})
    assert action["state"] == "CONFIRMED"
    assert action["confirmedOutcome"] == "FAILED_SAFE"
    assert store.get_command(command["commandUid"])["state"] == "FAILED"
    assert store.get_work_slot() is None
    status = updater.get_status()
    assert status["activeJobPermitCount"] == 0
    assert status["unreconciledPhysicalActionCount"] == 0
    assert all(
        event["event_type"] != "DELIVERY_COMPLETE"
        for event in store.list_pending_events()
    )
    updater.close()
    store.close()


@pytest.mark.parametrize("mismatch", ["mcu_command_uid", "round_index"])
def test_postclose_weight_rejects_wrong_action_identity_without_mutation(
    tmp_path,
    mismatch,
):
    """A foreign/stale weight fact cannot become the session's settlement."""

    store = make_store(tmp_path)
    session_uid = "53000000-0000-4000-8000-000000000201"
    authorize_uid = "53000000-0000-4000-8000-000000000202"
    original_context = {
        "session_uid": session_uid,
        "port_no": 1,
        "authorize_mcu_command_uid": authorize_uid,
        "round_index": 2,
        "phase": "WAITING_POSTCLOSE_WEIGHT",
        "negative_weight_threshold_grams": 500,
        "negative_weight_anomaly": False,
        "first_weight_grams": 1_000,
        "round_1_close_weight": 1_250,
        "round_2_open_weight": 1_250,
        "final_weight_grams": 1_250,
        "final_measurement_uid": (
            "53000000-0000-4000-8000-000000000203"
        ),
    }
    assert store.acquire_work_slot(
        "DELIVERY",
        session_uid,
        1,
        copy.deepcopy(original_context),
    )
    work = WorkManager(store, FakeUart(), None, FakePhotoManager())
    payload = {
        "mcuBootId": 42,
        "mcuEventSequence": 3,
        "uptimeMs": 3_000,
        "mcuCommandUid": authorize_uid,
        "sessionUid": session_uid,
        "portNo": 1,
        "roundIndex": 2,
        "measurementUid": "53000000-0000-4000-8000-000000000204",
        "measurementStatus": "STABLE",
        "weightValuePresent": True,
        "reportedWeightGrams": 1_700,
        "weightValueKind": "STABLE_WINDOW_MEAN",
        "measurementElapsedMs": 1_000,
        "sampleCount": 10,
        "calibrationVersion": 1,
        "weightSensorHealth": "OK",
        "faultCode": "NONE",
    }
    if mismatch == "mcu_command_uid":
        payload["mcuCommandUid"] = (
            "53000000-0000-4000-8000-000000000299"
        )
    else:
        payload["roundIndex"] = 3

    with pytest.raises((ValueError, JobSafetyError)):
        work.handle_mcu_event(
            {
                "message_name": "WORK_POSTCLOSE_WEIGHT_READY",
                "payload": payload,
            }
        )

    persisted = store.get_work_slot()
    assert persisted is not None
    assert persisted["context"] == original_context
    assert persisted["context"]["round_index"] == 2
    assert persisted["context"]["final_weight_grams"] == 1_250
    assert persisted["context"]["final_measurement_uid"] == (
        "53000000-0000-4000-8000-000000000203"
    )
    assert store.list_pending_events() == []
    store.close()
