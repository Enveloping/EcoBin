import json
import os
from datetime import datetime, timedelta, timezone

from command_processor import CommandProcessor
from edge_store import EdgeStore
from onenet_wire import (
    canonical_payload_sha256,
    decode_service_command,
)
from uart_link import compute_mcu_payload_sha256
from work_manager import WorkManager


class FakeUart:
    def __init__(self):
        self.calls = []
        self.command_result = None

    def apply_configuration(self, command, part_command_uids):
        self.calls.append((command, list(part_command_uids)))
        return {
            "acked": True,
            "commit_mcu_command_uid": part_command_uids[-1],
            "parts": [
                {
                    "message_name": "CONFIG_PART",
                    "mcu_command_uid": part_uid,
                    "acked": True,
                }
                for part_uid in part_command_uids
            ],
        }

    def send_command(self, message_name, values, *, mcu_command_uid=None):
        self.calls.append((message_name, dict(values), mcu_command_uid))
        if self.command_result is not None:
            return {
                "message_name": message_name,
                "mcu_command_uid": mcu_command_uid,
                **self.command_result,
            }
        return {
            "acked": True,
            "message_name": message_name,
            "mcu_command_uid": mcu_command_uid,
            "disposition": "ACCEPTED",
        }


class FakePhotoManager:
    def __init__(self, queue_result=True):
        self.captured = []
        self.queue_result = queue_result

    def capture_open_photos_async(self, work_uid):
        self.captured.append(("open", work_uid))
        return self.queue_result

    def capture_close_photos_async(self, work_uid):
        self.captured.append(("close", work_uid))
        return self.queue_result

    def capture_clean_photos_async(self, work_uid):
        self.captured.append(("clean", work_uid))
        return self.queue_result

    def get_slot_urls(self, work_uid):
        return {"CLOSE_OUTSIDE": "cos://after.jpg"}


def make_store(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    return store


def valid_configuration_command():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "apply-configuration.service-wire.json",
    )
    with open(path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    command = decode_service_command(body["identifier"], body["params"])
    now = datetime.now(timezone.utc)
    command["issuedAt"] = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["payload"]["config"]["mcuPayloadSha256"] = compute_mcu_payload_sha256(
        command["payload"]
    )
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    return command


def valid_service_command(example_name):
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        example_name,
    )
    with open(path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    command = decode_service_command(body["identifier"], body["params"])
    now = datetime.now(timezone.utc)
    command["issuedAt"] = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    return command


def mark_configuration_applied(store):
    command = valid_configuration_command()
    part_uids = [
        str(__import__("uuid").uuid4())
        for _ in range(len(command["payload"]["ports"]) + 3)
    ]
    assert store.save_configuration_edge(command, part_uids) == "ACCEPTED"
    assert store.apply_configuration_result({
        "mcuCommandUid": part_uids[-1],
        "applicationUid": command["payload"]["applicationUid"],
        "status": "APPLIED",
        "configVersion": command["payload"]["config"]["version"],
        "contentSha256": command["payload"]["config"]["contentSha256"],
        "mcuPayloadSha256": command["payload"]["config"]["mcuPayloadSha256"],
        "faultCode": "NONE",
    }) == "ACCEPTED"
    return command


def test_apply_configuration_consumes_inbox_and_waits_for_mcu_result(tmp_path):
    store = make_store(tmp_path)
    uart = FakeUart()
    processor = CommandProcessor(store, uart)
    command = valid_configuration_command()
    store.receive_command(command["commandUid"], command["commandType"], command)

    assert processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "WAITING_MCU_RESULT"
    configuration = store.get_configuration(command["payload"]["applicationUid"])
    assert configuration["state"] == "WAITING_MCU_RESULT"
    assert len(configuration["part_command_uids"]) == len(command["payload"]["ports"]) + 3
    events = store._conn.execute(
        "SELECT * FROM event_outbox WHERE event_type='CONFIGURATION_PROGRESS'"
    ).fetchall()
    assert len(events) == 1
    edge_saved = json.loads(events[0]["payload_json"])
    assert edge_saved["payload"]["stage"] == "EDGE_SAVED"
    store.close()


def test_configuration_apply_result_completes_command_and_creates_event(tmp_path):
    store = make_store(tmp_path)
    uart = FakeUart()
    processor = CommandProcessor(store, uart)
    command = valid_configuration_command()
    store.receive_command(command["commandUid"], command["commandType"], command)
    processor.process_next()
    configuration = store.get_configuration(command["payload"]["applicationUid"])
    commit_uid = configuration["part_command_uids"][-1]

    processor.process_mcu_event({
        "message_name": "CONFIG_APPLY_RESULT",
        "message_type": 20,
        "source_tx_sequence": 9,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 1,
            "uptimeMs": 1000,
            "mcuCommandUid": commit_uid,
            "applicationUid": command["payload"]["applicationUid"],
            "status": "APPLIED",
            "configVersion": command["payload"]["config"]["version"],
            "contentSha256": command["payload"]["config"]["contentSha256"],
            "mcuPayloadSha256": command["payload"]["config"]["mcuPayloadSha256"],
            "faultCode": "NONE",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert store.get_configuration(command["payload"]["applicationUid"])["state"] == "APPLIED"
    assert store.get_state("applied_config_version") == "8"
    events = store._conn.execute(
        "SELECT payload_json FROM event_outbox "
        "WHERE event_type='CONFIGURATION_PROGRESS' ORDER BY edge_event_sequence"
    ).fetchall()
    assert [json.loads(row["payload_json"])["payload"]["stage"] for row in events] == [
        "EDGE_SAVED",
        "APPLIED",
    ]
    store.close()


def test_invalid_previously_accepted_command_is_failed_without_uart(tmp_path):
    store = make_store(tmp_path)
    uart = FakeUart()
    processor = CommandProcessor(store, uart)
    invalid = valid_configuration_command()
    invalid["commandUid"] = "12"
    store.receive_command("12", "APPLY_CONFIGURATION", invalid)

    processor.process_next()

    command = store.get_command("12")
    assert command["state"] == "FAILED"
    assert command["last_error"] == "INVALID_COMMAND_IDENTITY"
    assert uart.calls == []
    store.close()


def test_start_delivery_is_persisted_before_waiting_for_mcu_result(tmp_path):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-delivery-session.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)

    assert processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "WAITING_MCU_RESULT"
    slot = store.get_work_slot()
    assert slot["work_type"] == "DELIVERY"
    assert slot["work_uid"] == command["payload"]["sessionUid"]
    message_name, values, command_uid = uart.calls[0]
    assert message_name == "START_DELIVERY_SESSION"
    assert command_uid == inbox["mcu_command_uid"]
    assert values["deliveryAutoCloseMs"] == 120000
    assert values["startExecutionWindowMs"] > 0
    store.close()


def test_start_delivery_ack_timeout_requires_reconciliation(tmp_path):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    uart = FakeUart()
    uart.command_result = {"acked": False, "error": "TIMEOUT"}
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-delivery-session.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)

    assert processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "UART_ACK_RESULT_UNKNOWN"
    slot = store.get_work_slot()
    assert slot["work_type"] == "DELIVERY"
    assert slot["work_uid"] == command["payload"]["sessionUid"]
    assert slot["context"]["phase"] == "START_RESULT_UNKNOWN"
    store.close()


def test_unstable_preopen_and_dropped_photos_still_authorize_first_open(
    tmp_path,
):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    uart = FakeUart()
    photos = FakePhotoManager(queue_result=False)
    work = WorkManager(store, uart, None, photos)
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-delivery-session.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)
    processor.process_next()
    start_mcu_command_uid = store.get_command(
        command["commandUid"]
    )["mcu_command_uid"]
    measurement_uid = "52000000-0000-4000-8000-000000000001"

    processor.process_mcu_event({
        "message_name": "WORK_PREOPEN_WEIGHT_READY",
        "message_type": 48,
        "source_tx_sequence": 9,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 1,
            "uptimeMs": 1000,
            "mcuCommandUid": start_mcu_command_uid,
            "sessionUid": command["payload"]["sessionUid"],
            "portNo": command["payload"]["portNo"],
            "roundIndex": 0,
            "measurementUid": measurement_uid,
            "measurementStatus": "UNSTABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": 1234,
            "weightValueKind": "LAST_FOUR_MEAN",
            "measurementElapsedMs": 6000,
            "sampleCount": 60,
            "calibrationVersion": 1,
            "weightSensorHealth": "OK",
            "faultCode": "WEIGHT_UNSTABLE",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert photos.captured == [("open", command["payload"]["sessionUid"])]
    message_name, values, _ = uart.calls[-1]
    assert message_name == "AUTHORIZE_DELIVERY_FIRST_OPEN"
    assert values["firstPreOpenMeasurementUid"] == measurement_uid
    assert values["parentStartCommandUid"] == start_mcu_command_uid
    assert values["remainingStartAuthorizationMs"] > 0
    store.close()


def test_delivery_complete_reports_latched_command_without_pulse_duration(
    tmp_path,
):
    store = make_store(tmp_path)
    session_uid = "53000000-0000-4000-8000-000000000001"
    measurement_uid = "53000000-0000-4000-8000-000000000002"
    measurement = {
        "measurementUid": measurement_uid,
        "measurementStatus": "STABLE",
        "weightValuePresent": True,
        "reportedWeightGrams": 1500,
        "weightValueKind": "STABLE_WINDOW_MEAN",
        "measurementElapsedMs": 1000,
        "sampleCount": 10,
        "calibrationVersion": 1,
        "weightSensorHealth": "OK",
        "faultCode": "NONE",
        "mcuBootId": 42,
        "mcuEventSequence": 7,
    }
    assert store.acquire_work_slot(
        "DELIVERY",
        session_uid,
        1,
        {
            "session_uid": session_uid,
            "port_no": 1,
            "start_command_uid": (
                "53000000-0000-4000-8000-000000000003"
            ),
            "deployment_code": "Dp_demo_01",
            "unit_price_ten_thousandths": 4500,
            "config": {
                "version": 8,
                "contentSha256": "a" * 64,
                "mcuPayloadSha256": "b" * 64,
            },
            "first_measurement": measurement,
            "final_measurement": measurement,
            "first_weight_grams": 1000,
            "final_weight_grams": 1500,
            "round_index": 1,
            "round_1_measurement_uid": measurement_uid,
            "last_delivery_door_command": "CLOSE",
            "last_delivery_door_output_status": "COMMAND_DISPATCHED",
            "delivery_door_physical_state_basis": "NOT_OBSERVABLE",
            "negative_weight_anomaly": False,
        },
    )
    work = WorkManager(
        store,
        FakeUart(),
        None,
        FakePhotoManager(),
    )

    work.handle_mcu_event(
        {
            "message_name": "DELIVERY_SELECTION",
            "payload": {
                "sessionUid": session_uid,
                "portNo": 1,
                "roundIndex": 1,
                "postCloseMeasurementUid": measurement_uid,
                "selection": "END",
            },
        }
    )

    envelope = json.loads(store.list_pending_events()[-1]["payload_json"])
    door_fact = envelope["payload"]["finalDoorCommand"]
    assert door_fact == {
        "command": "CLOSE",
        "outputStatus": "COMMAND_DISPATCHED",
        "physicalStateBasis": "NOT_OBSERVABLE",
    }
    store.close()


def test_clean_preunlock_failure_is_reported_but_does_not_block_unlock(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "WAITING_MCU_RESULT"
    start_mcu_command_uid = inbox["mcu_command_uid"]
    processor.process_mcu_event({
        "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
        "message_type": 52,
        "source_tx_sequence": 10,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 2,
            "uptimeMs": 2000,
            "mcuCommandUid": start_mcu_command_uid,
            "operationUid": command["payload"]["operationUid"],
            "portNo": command["payload"]["portNo"],
            "measurementUid": "53000000-0000-4000-8000-000000000001",
            "measurementStatus": "DISCONNECTED",
            "weightValuePresent": False,
            "reportedWeightGrams": 0,
            "weightValueKind": "NONE",
            "measurementElapsedMs": 0,
            "sampleCount": 0,
            "calibrationVersion": 1,
            "weightSensorHealth": "DISCONNECTED",
            "faultCode": "WEIGHT_DISCONNECTED",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    message_name, values, _ = uart.calls[-1]
    assert message_name == "UNLOCK_CLEAN_DOOR"
    assert values["cleanActionSequence"] == 0
    assert values["recoveryGeneration"] == 0
    assert values["unlockPulseMs"] == 1000
    assert values["remainingOperationWindowMs"] > 0
    assert values["parentCommandUid"] == start_mcu_command_uid
    store.close()


def test_clean_final_weight_failure_still_allows_manual_completion(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeUart()
    photos = FakePhotoManager()
    work = WorkManager(store, uart, None, photos)
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)
    processor.process_next()
    start_uid = store.get_command(command["commandUid"])["mcu_command_uid"]
    operation_uid = command["payload"]["operationUid"]
    processor.process_mcu_event({
        "message_name": "WORK_PREUNLOCK_WEIGHT_READY",
        "message_type": 52,
        "source_tx_sequence": 10,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 2,
            "uptimeMs": 2000,
            "mcuCommandUid": start_uid,
            "operationUid": operation_uid,
            "portNo": command["payload"]["portNo"],
            "measurementUid": "53000000-0000-4000-8000-000000000001",
            "measurementStatus": "STABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": 50000,
            "weightValueKind": "STABLE_WINDOW_MEAN",
            "measurementElapsedMs": 1000,
            "sampleCount": 10,
            "calibrationVersion": 1,
            "weightSensorHealth": "OK",
            "faultCode": "NONE",
        },
    })
    processor.process_mcu_event({
        "message_name": "CLEAN_FINISH_REQUESTED",
        "message_type": 55,
        "source_tx_sequence": 11,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 3,
            "uptimeMs": 3000,
            "operationUid": operation_uid,
            "portNo": command["payload"]["portNo"],
            "cleanActionSequence": 1,
        },
    })
    final_measurement_uid = "54000000-0000-4000-8000-000000000001"
    processor.process_mcu_event({
        "message_name": "CLEAN_FINAL_WEIGHT_READY",
        "message_type": 56,
        "source_tx_sequence": 12,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 4,
            "uptimeMs": 9000,
            "operationUid": operation_uid,
            "portNo": command["payload"]["portNo"],
            "cleanActionSequence": 1,
            "measurementUid": final_measurement_uid,
            "measurementStatus": "DISCONNECTED",
            "weightValuePresent": False,
            "reportedWeightGrams": 0,
            "weightValueKind": "NONE",
            "measurementElapsedMs": 0,
            "sampleCount": 0,
            "calibrationVersion": 1,
            "weightSensorHealth": "DISCONNECTED",
            "faultCode": "WEIGHT_DISCONNECTED",
        },
    })
    processor.process_mcu_event({
        "message_name": "CLEAN_COMPLETION_CONFIRMED",
        "message_type": 62,
        "source_tx_sequence": 13,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 5,
            "uptimeMs": 10000,
            "operationUid": operation_uid,
            "portNo": command["payload"]["portNo"],
            "cleanActionSequence": 1,
            "finalMeasurementUid": final_measurement_uid,
            "lockPowerState": "DEENERGIZED",
            "solenoidHealth": "OK",
            "cleanDoorStateBasis": "CLEANER_CONFIRMATION",
            "cleanerPhysicalCloseConfirmed": True,
        },
    })

    slot = store.get_work_slot()
    assert slot["work_state"] == "COMPLETING"
    event = store.list_pending_events()[-1]
    envelope = json.loads(event["payload_json"])
    assert envelope["eventType"] == "CLEAN_COMPLETE"
    assert envelope["edgeEventSequence"] == event["edge_event_sequence"]
    event_payload = envelope["payload"]
    assert (
        event_payload["cleanerConfirmedFinalMeasurement"]["status"]
        == "DISCONNECTED"
    )
    assert (
        event_payload["cleanLockAndManualDoorConfirmation"][
            "cleanerPhysicalCloseConfirmed"
        ]
        is True
    )
    assert (
        event_payload["cleanLockAndManualDoorConfirmation"][
            "physicalDoorStateBasis"
        ]
        == "CLEANER_CONFIRMATION"
    )
    store.close()


def test_fullness_no_echo_is_reported_as_clear_without_fault(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command("sample-fullness.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "WAITING_MCU_RESULT"
    mcu_command_uid = inbox["mcu_command_uid"]
    message_name, values, _ = uart.calls[-1]
    assert message_name == "SAMPLE_FULLNESS"
    assert values["measurementTimeoutMs"] == 6000
    processor.process_mcu_event({
        "message_name": "FULLNESS_SAMPLE_RESULT",
        "message_type": 57,
        "source_tx_sequence": 14,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 6,
            "uptimeMs": 12000,
            "mcuCommandUid": mcu_command_uid,
            "detectionUid": command["payload"]["detectionUid"],
            "portNo": command["payload"]["portNo"],
            "sampleRole": "INITIAL",
            "fullnessSensorKind": "ULTRASONIC",
            "fullnessSensorValue": "CLEAR",
            "fullnessSampleBasis": "NO_ECHO_CLEAR_FALLBACK",
            "representativeDistancePresent": False,
            "representativeDistanceMm": 0,
            "requestedSampleCount": 5,
            "validSampleCount": 0,
            "measurementUid": "55000000-0000-4000-8000-000000000001",
            "measurementStatus": "DISCONNECTED",
            "weightValuePresent": False,
            "reportedWeightGrams": 0,
            "weightValueKind": "NONE",
            "measurementElapsedMs": 0,
            "sampleCount": 0,
            "calibrationVersion": 1,
            "weightSensorHealth": "DISCONNECTED",
            "faultCode": "WEIGHT_DISCONNECTED",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert store.get_work_slot() is None
    assert store.list_active_faults() == []
    event = json.loads(store.list_pending_events()[-1]["payload_json"])
    assert event["eventType"] == "FULLNESS_SAMPLE_COMPLETE"
    assert event["payload"]["fullnessSensorValue"] == "CLEAR"
    assert (
        event["payload"]["fullnessSampleBasis"]
        == "NO_ECHO_CLEAR_FALLBACK"
    )
    assert event["payload"]["representativeDistanceMm"] is None
    store.close()


def test_unstable_empty_bag_baseline_keeps_value_and_quality(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    command = valid_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    store.receive_command(command["commandUid"], command["commandType"], command)

    processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "WAITING_MCU_RESULT"
    mcu_command_uid = inbox["mcu_command_uid"]
    message_name, values, _ = uart.calls[-1]
    assert message_name == "MEASURE_BASELINE"
    assert values["measurementTimeoutMs"] == 6000
    processor.process_mcu_event({
        "message_name": "BASELINE_MEASUREMENT_RESULT",
        "message_type": 58,
        "source_tx_sequence": 15,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 7,
            "uptimeMs": 18000,
            "mcuCommandUid": mcu_command_uid,
            "measurementUid": command["payload"]["measurementUid"],
            "portNo": command["payload"]["portNo"],
            "measurementStatus": "UNSTABLE",
            "weightValuePresent": True,
            "reportedWeightGrams": 1180,
            "weightValueKind": "LAST_FOUR_MEAN",
            "measurementElapsedMs": 6000,
            "sampleCount": 60,
            "calibrationVersion": 1,
            "weightSensorHealth": "OK",
            "faultCode": "WEIGHT_UNSTABLE",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert store.get_work_slot() is None
    event = json.loads(store.list_pending_events()[-1]["payload_json"])
    measurement = event["payload"]["totalWeightMeasurement"]
    assert measurement["reportedWeightGrams"] == 1180
    assert measurement["status"] == "UNSTABLE"
    assert measurement["weightValueAvailable"] is True
    assert measurement["weightValueKind"] == "LAST_FOUR_MEAN"
    store.close()


def test_end_clean_before_unlock_releases_reserved_operation(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    start = valid_service_command("start-clean-operation.service-wire.json")
    store.receive_command(start["commandUid"], start["commandType"], start)
    processor.process_next()
    end = valid_service_command("end-clean-before-unlock.service-wire.json")
    store.receive_command(end["commandUid"], end["commandType"], end)

    processor.process_next()

    assert store.get_command(end["commandUid"])["state"] == "COMPLETED"
    assert store.get_work_slot() is None
    message_name, values, _ = uart.calls[-1]
    assert message_name == "END_CLEAN_BEFORE_UNLOCK"
    assert values["operationUid"] == start["payload"]["operationUid"]
    assert values["reason"] == "CLEANER_CANCELLED"
    store.close()


def test_cloud_clean_resume_does_not_reset_already_recovered_window(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    deadline = (
        datetime.now(timezone.utc) + timedelta(minutes=12)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command = valid_service_command("resume-clean-operation.service-wire.json")
    operation_uid = command["payload"]["operationUid"]
    store.acquire_work_slot(
        "CLEAN",
        operation_uid,
        command["payload"]["portNo"],
        {
            "operation_uid": operation_uid,
            "port_no": command["payload"]["portNo"],
            "new_bag_uid": command["payload"]["newBagUid"],
            "config": command["payload"]["config"],
            "operation_deadline": deadline,
            "recovery_generation": 1,
            "action_sequence": 4,
            "phase": "CLEAN_RECOVERY_REQUIRED",
        },
    )
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)
    store.receive_command(command["commandUid"], command["commandType"], command)

    processor.process_next()

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    slot = store.get_work_slot()
    assert slot["context"]["operation_deadline"] == deadline
    assert slot["context"]["recovery_generation"] == 1
    assert uart.calls == []
    store.close()


def test_smoke_alarm_is_recorded_without_blocking_delivery(tmp_path):
    store = make_store(tmp_path)
    store.set_state("applied_config_version", "8")
    store.set_state("applied_config_content_sha256", "a" * 64)
    uart = FakeUart()
    work = WorkManager(store, uart, None, FakePhotoManager())
    processor = CommandProcessor(store, uart, work)

    processor.process_mcu_event({
        "message_name": "SAFETY_SENSOR_EVENT",
        "message_type": 60,
        "source_tx_sequence": 16,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 8,
            "uptimeMs": 19000,
            "smokeState": "ALARM",
            "smokeSensorHealth": "OK",
            "faultCode": "NONE",
            "workType": "NONE",
            "workUid": "00000000-0000-0000-0000-000000000000",
            "portNo": 1,
        },
    })

    assert store.get_state("smoke_state") == "ALARM"
    assert store.list_active_faults() == []
    command = valid_service_command("start-delivery-session.service-wire.json")
    store.receive_command(command["commandUid"], command["commandType"], command)
    processor.process_next()
    assert store.get_command(command["commandUid"])["state"] == (
        "WAITING_MCU_RESULT"
    )
    store.close()
