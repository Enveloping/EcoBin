"""Actual MCU result -> original cloud command -> atomic reliable report."""
import json
from pathlib import Path
import sqlite3
import uuid
import pytest

import uart2_protocol as uart
from hardware.tests.test_mcu_work_preparation import library, runtime
from hardware.tests.test_mcu_delivery_execution import executed_action_case
from hardware.tests.test_native_work_recovery import RecoveryWire
from onenet_wire import canonical_payload_sha256, encode_event_post
from contractlib import JsonSchemaSubsetValidator
from validate_contracts import _validate_event_semantics


def finish_with_samples(case, runtime, samples, *, window_expired=False):
    from hardware.tests.test_native_configuration import inputs
    from hardware.tests.test_mcu_work_preparation import take_samples
    wire = case.wire = RecoveryWire(case, runtime)
    lib, endpoint, *_ = runtime
    if case.clean:
        wire.intent(0, "CLEAN_FINISH_REQUESTED")
        wire.advance(0)
    else:
        wire.advance(inputs()["device"]["deliveryDoorTravelWaitMs"])
    began = wire.now
    wire.now = take_samples(runtime, samples, start=began, measurement=2)
    if len(samples) != 5:
        wire.advance(began + 5000 - wire.now)
    name = "CLEAN_FINAL_WEIGHT_READY" if case.clean else "WORK_POSTCLOSE_WEIGHT_READY"
    row = wire.custody(name, 1)
    final = uart.decode_payload(name, row["payload"])
    wire.advance(0)
    if case.clean:
        assert lib.McuCleanExecution_Confirm(case.execution, endpoint, uuid.UUID(case.permit.work_uid).bytes,
            1, uuid.UUID(final["measurementUid"]).bytes, wire.now)
        wire.custody("CLEAN_COMPLETION_CONFIRMED", 1)
    elif samples:
        if window_expired:
            wire.advance(case.start["continueDeliveryWaitMs"])
        else:
            assert lib.McuDeliveryExecution_Select(case.execution, endpoint, uuid.UUID(final["measurementUid"]).bytes, 2, wire.now)
        wire.custody("DELIVERY_SELECTION", 1)
    wire.advance(0)
    return wire.handoff_result()


@pytest.mark.parametrize("clean", [False, True])
def test_multiple_rounds_report_the_original_first_and_current_final_not_intermediate_weight(runtime, tmp_path, clean):
    from hardware.tests.test_native_result_execution import next_cycle
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=clean, cloud_command_factory=original_command) as case:
        next_cycle(case, runtime)
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        validate_event(event)
        payload = event["payload"]
        if clean:
            assert payload["removedNetWeightGrams"] == -400
            assert payload["newBaselineWeightGrams"] == 900
            assert payload["cleanActionSequence"] == 3
        else:
            assert payload["deliveryNetWeightGrams"] == 400
            assert payload["finalPostCloseMeasurement"]["reportedWeightGrams"] == 900
        assert len(case.store.list_pending_events()) == 1
        assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("clean", [False, True])
def test_actual_five_second_median_preserves_original_identity_and_quality_in_report(runtime, tmp_path, clean):
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=clean, cloud_command_factory=original_command) as case:
        saved = finish_with_samples(case, runtime, [700, 1000] * 10)
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["finalKind"] == "TIMEOUT_MEDIAN"
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        validate_event(event)
        payload = event["payload"]
        weight = payload["cleanerConfirmedFinalMeasurement" if clean else "finalPostCloseMeasurement"]
        assert weight["reportedWeightGrams"] == 850
        assert weight["status"] == "UNSTABLE" and weight["weightValueKind"] == "TIMEOUT_MEDIAN"
        assert weight["sampleCount"] == 20 and weight["measurementElapsedMs"] == 5000
        assert weight["measurementUid"] == result["finalMeasurementUid"]
        assert payload["removedNetWeightGrams" if clean else "deliveryNetWeightGrams"] == (-350 if clean else 350)


def test_clean_confirmed_after_real_final_timeout_reports_missing_weight_not_old_baseline(runtime, tmp_path):
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=True, cloud_command_factory=original_command) as case:
        saved = finish_with_samples(case, runtime, [])
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["physicalCloseConfirmed"] and result["finalKind"] == "UNAVAILABLE"
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        assert report["state"] == "REPORT_CREATED"
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        validate_event(event)
        payload = event["payload"]
        final = payload["cleanerConfirmedFinalMeasurement"]
        assert final["status"] == "TIMEOUT" and final["faultCode"] == "WEIGHT_TIMEOUT"
        assert final["weightValueAvailable"] is False and final["reportedWeightGrams"] is None
        assert final["sampleCount"] == 0 and final["measurementElapsedMs"] == 5000
        assert final["measurementUid"] == result["finalMeasurementUid"]
        assert payload["removedNetWeightGrams"] is None and payload["newBaselineWeightGrams"] is None
        assert payload["cleanerCompletionConfirmed"] is True
        assert case.store.get_work_slot() == case.occupancy


def test_closed_delivery_with_real_final_timeout_reports_terminal_weight_failure_without_net_value(runtime, tmp_path):
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        finish_with_samples(case, runtime, [])
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        assert report["state"] == "REPORT_CREATED"
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        validate_event(event)
        payload = event["payload"]
        assert payload["completionReason"] == "TERMINAL_WEIGHT_FAILURE"
        assert payload["manualReviewRequired"] is False
        assert payload["deliveryNetWeightGrams"] is None
        assert payload["firstPreOpenMeasurement"]["reportedWeightGrams"] == 500
        assert payload["finalPostCloseMeasurement"]["reportedWeightGrams"] is None
        assert payload["finalPostCloseMeasurement"]["faultCode"] == "WEIGHT_TIMEOUT"
        assert payload["finalDoorCommand"]["command"] == "CLOSE"
        assert payload["finalDoorCommand"]["outputStatus"] == "COMMAND_DISPATCHED"
        assert case.store.get_work_slot() == case.occupancy


def test_timeout_result_does_not_substitute_for_missing_close_output(runtime, tmp_path):
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        finish_with_samples(case, runtime, [])
        events = case.store.list_native_work_actuator_events(case.permit.work_uid)
        close = next(row for row in events if uart.decode_payload(row["message_name"], row["payload"])["command"] == "CLOSE")
        case.store._conn.execute("PRAGMA foreign_keys=OFF")
        with case.store.transaction() as conn:
            conn.execute("DELETE FROM native_actuator_event WHERE mcu_boot_id=? AND event_sequence=?",
                (close["mcu_boot_id"], close["event_sequence"]))
        case.store._conn.execute("PRAGMA foreign_keys=ON")
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        assert report["state"] != "REPORT_CREATED"
        assert any(item["role"] == "actuatorOutput" for item in report["missing"])
        assert case.store.list_pending_events() == []
        assert case.safety.get_physical_action(case.action.action_uid)["state"] == "ARMED"


def test_selection_window_expiry_reports_the_original_final_measurement(runtime, tmp_path):
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        finish_with_samples(case, runtime, [700] * 5, window_expired=True)
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        validate_event(event)
        assert event["payload"]["completionReason"] == "SELECTION_WINDOW_EXPIRED"
        assert event["payload"]["deliveryNetWeightGrams"] == 200


@pytest.mark.parametrize("clean", [False, True])
def test_report_freezes_real_photo_queue_snapshot_without_uploading_or_rewriting_later(runtime, tmp_path, clean):
    from native_result_report import NativeResultReporter
    from photo_manager import PhotoManager
    from onenet_wire import WORK_PHOTO_SLOTS
    with executed_action_case(runtime, tmp_path, clean_work=clean, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        wire.finish_clean() if clean else wire.finish_delivery()
        work_type = "CLEAN_OPERATION" if clean else "DELIVERY_SESSION"
        slots = WORK_PHOTO_SLOTS[work_type]
        photos = PhotoManager(case.store, photo_dir=str(tmp_path / "photos"), start_upload_worker=False,
            outside_camera_source="test-outer", inside_camera_source="test-inner")
        try:
            uid = str(uuid.uuid4())
            case.store.register_photo(uid, slots[0], str(tmp_path / "not-opened.jpg"), work_uid=case.permit.work_uid,
                work_type=work_type, device_name="device-1", content_sha256="a" * 64, size_bytes=100,
                captured_at="2026-09-13T00:00:00.000Z")
            expected = photos.get_completion_photo_facts(case.permit.work_uid, work_type)
            reporter = NativeResultReporter(case.store, case.safety, device_name="device-1", photo_manager=photos)
            report = reporter.prepare(case.permit, case.start["mcuCommandUid"])
            event = case.store.get_event(report["eventUid"])
            value = json.loads(event["payload_json"])
            validate_event(value)
            assert value["payload"]["photos"] == expected
            assert expected[0]["photoUid"] == uid and expected[0]["url"] is None
            case.store.mark_photo_dead(uid, "PHOTO_UPLOAD_FAILED")
            assert photos.get_completion_photo_facts(case.permit.work_uid, work_type) != expected
            assert reporter.prepare(case.permit, case.start["mcuCommandUid"]) == report
            assert case.store.get_event(report["eventUid"]) == event
            assert case.store.get_work_slot() == case.occupancy
        finally:
            photos.close()


def validate_event(event):
    validator = JsonSchemaSubsetValidator()
    schema = Path(__file__).resolve().parents[2] / "contracts/onenet/events/events.schema.json"
    validator.validate(event, schema)
    mapping = json.loads((schema.parents[1] / "thing-model.mapping.yaml").read_text(encoding="utf-8"))
    _validate_event_semantics(event, mapping)


def original_command(command, start, config):
    clean = command["commandType"] == "START_CLEAN_OPERATION"
    payload = dict(command["payload"], config=dict(version=config["config_version"],
        contentSha256=config["content_sha256"], mcuPayloadSha256=config["expected_sha256"]))
    if clean:
        payload.update(oldBagUid="88888888-8888-4888-8888-888888888888", oldBaselineWeightGrams=80,
            newBagUid="99999999-9999-4999-8999-999999999999", operationWindowMs=start["operationWindowMs"], recoveryGeneration=0)
    else:
        payload.update(bagUid="88888888-8888-4888-8888-888888888888", unitPriceTenThousandths=1500,
            **{field: start[field] for field in ("continueDeliveryWaitMs", "negativeWeightThresholdGrams", "deliveryAutoCloseMs")})
    return {key: value for key, value in command.items() if key != "cosGrant"} | dict(
        schemaVersion=2, payloadSchemaVersion=2, payload=payload, payloadSha256=canonical_payload_sha256(payload),
        target=dict(type="CLEAN_OPERATION" if clean else "DELIVERY_SESSION", uid=payload["operationUid" if clean else "sessionUid"]))


def test_actual_delivery_result_creates_one_reliable_report_from_original_authority(runtime, tmp_path):
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        wire = case.wire = RecoveryWire(case, runtime)
        saved = wire.finish_delivery()
        from native_result_report import NativeResultReporter
        reporter = NativeResultReporter(case.store, case.safety, device_name="device-1")
        before = len(wire.sent)
        created = reporter.prepare(case.permit, case.start["mcuCommandUid"])
        assert created["state"] == "REPORT_CREATED"
        events = case.store.list_pending_events()
        assert len(events) == 1 and events[0]["event_type"] == "DELIVERY_COMPLETE"
        event = json.loads(events[0]["payload_json"])
        value = event["payload"]
        assert value["sessionUid"] == case.permit.work_uid
        assert value["unitPriceTenThousandths"] == 1500
        assert value["deliveryNetWeightGrams"] == 200
        assert value["firstPreOpenMeasurement"]["reportedWeightGrams"] == 500
        assert value["finalPostCloseMeasurement"]["reportedWeightGrams"] == 700
        assert value["completionReason"] == "USER_ENDED"
        assert value["manualReviewRequired"] is False
        assert all(photo["url"] is None and photo["status"] == "UPLOAD_PENDING" for photo in value["photos"])
        assert len(value["photos"]) == 4
        assert encode_event_post("DELIVERY_COMPLETE", event)
        validate_event(event)
        assert reporter.prepare(case.permit, case.start["mcuCommandUid"]) == created
        assert case.store.list_pending_events() == events
        assert case.store.list_native_result_report_tasks()[0]["state"] == "REPORT_CREATED"
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert len(wire.sent) == before


@pytest.mark.parametrize("missing", ["cloud", "process", "no_result"])
def test_missing_original_authority_or_evidence_stays_local_without_confirming_actions(runtime, tmp_path, missing):
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=False,
            cloud_command_factory=None if missing == "cloud" else original_command) as case:
        if missing != "no_result":
            RecoveryWire(case, runtime).finish_delivery()
        if missing == "process":
            case.store._conn.execute("PRAGMA foreign_keys=OFF")
            with case.store.transaction() as conn:
                conn.execute("DELETE FROM native_process_receipt")
            case.store._conn.execute("PRAGMA foreign_keys=ON")
        state = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        assert state["state"] != "REPORT_CREATED"
        assert case.store.list_pending_events() == []
        assert case.safety.get_physical_action(case.action.action_uid)["state"] == "ARMED"
        assert case.store.get_work_slot() == case.occupancy


def test_reopened_database_validates_report_and_current_device_before_duplicate_return(runtime, tmp_path):
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        RecoveryWire(case, runtime).finish_delivery()
        NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        with pytest.raises(ValueError):
            NativeResultReporter(case.store, case.safety, device_name="another-device").prepare(case.permit, case.start["mcuCommandUid"])


@pytest.mark.parametrize("clean", [False, True])
def test_v30_result_custody_migrates_without_changing_task_or_claiming_a_report(runtime, tmp_path, clean):
    from edge_store import EdgeStore, CURRENT_SCHEMA_VERSION
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=clean, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        saved = wire.finish_clean() if clean else wire.finish_delivery()
        task = case.store.list_native_result_report_tasks()[0]
        # Reproduce the actual previous schema at the SQLite boundary.
        with case.store.transaction() as conn:
            conn.execute("DROP TABLE native_result_confirmation")
            conn.execute("DROP TABLE native_result_report_outbox")
            conn.execute("""CREATE TABLE native_result_report_outbox (
                task_uid TEXT NOT NULL UNIQUE, mcu_boot_id INTEGER NOT NULL, result_sequence INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'PENDING_CLASSIFICATION' CHECK(state='PENDING_CLASSIFICATION'),
                created_at TEXT NOT NULL DEFAULT(datetime('now')), PRIMARY KEY(mcu_boot_id,result_sequence),
                FOREIGN KEY(mcu_boot_id,result_sequence) REFERENCES native_mcu_result(mcu_boot_id,result_sequence))""")
            conn.execute("INSERT INTO native_result_report_outbox(task_uid,mcu_boot_id,result_sequence,state,created_at) VALUES(?,?,?,?,?)",
                tuple(task[key] for key in ("task_uid", "mcu_boot_id", "result_sequence", "state", "created_at")))
            conn.execute("DELETE FROM schema_version WHERE version>30")
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db"))
        case.store.initialize()
        assert CURRENT_SCHEMA_VERSION == 39
        assert case.store.list_native_result_report_tasks() == [task]
        assert case.store.get_native_mcu_result(saved["mcu_boot_id"], saved["result_sequence"]) == saved
        assert case.store.list_pending_events() == []
        assert NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(
            case.permit, case.start["mcuCommandUid"])["eventUid"] == task["task_uid"]


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("failure_point", ["event", "task"])
def test_failed_report_commit_rolls_back_both_records_and_retries_after_restart(runtime, tmp_path, clean, failure_point):
    from edge_store import EdgeStore
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=clean, cloud_command_factory=original_command) as case:
        wire = RecoveryWire(case, runtime)
        wire.finish_clean() if clean else wire.finish_delivery()
        # Fault at the database boundary after the actual MCU result was saved.
        table, operation = ("event_outbox", "INSERT") if failure_point == "event" else ("native_result_report_outbox", "UPDATE")
        with case.store.transaction() as conn:
            conn.execute(f"CREATE TRIGGER reject_report BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'injected report failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match="injected report failure"):
            NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        assert case.store.list_pending_events() == []
        assert case.store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
        assert case.store.get_work_slot() == case.occupancy
        with case.store.transaction() as conn:
            conn.execute("DROP TRIGGER reject_report")
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db"))
        case.store.initialize()
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        event = case.store.get_event(report["eventUid"])
        assert event["edge_event_sequence"] == 1
        case.store.close()
        case.store = EdgeStore(str(tmp_path / "edge.db"))
        case.store.initialize()
        assert NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(
            case.permit, case.start["mcuCommandUid"]) == report
        assert case.store.get_event(report["eventUid"]) == event
        assert case.store.get_work_slot() == case.occupancy


@pytest.mark.parametrize("corruption", ["result_payload", "result_index", "event_payload", "conflict", "start", "permit"])
def test_saved_report_never_hides_corrupted_original_custody(runtime, tmp_path, corruption):
    from native_result_report import NativeResultReporter
    from dataclasses import replace
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        saved = RecoveryWire(case, runtime).finish_delivery()
        reporter = NativeResultReporter(case.store, case.safety, device_name="device-1")
        report = reporter.prepare(case.permit, case.start["mcuCommandUid"])
        with case.store.transaction() as conn:
            if corruption == "result_payload":
                conn.execute("UPDATE native_mcu_result SET payload=zeroblob(199)")
            elif corruption == "result_index":
                conn.execute("UPDATE native_mcu_result SET work_uid=?", ("99999999-9999-4999-8999-999999999999",))
            elif corruption == "event_payload":
                conn.execute("UPDATE event_outbox SET payload_json='{}'")
            elif corruption == "conflict":
                conn.execute("INSERT INTO native_mcu_result_conflict(mcu_boot_id,result_sequence,payload) VALUES (?,?,?)",
                    (saved["mcu_boot_id"], saved["result_sequence"], b'\0' * 199))
            elif corruption == "start":
                conn.execute("UPDATE native_mcu_command SET payload=zeroblob(length(payload)) WHERE command_uid=?",
                    (case.start["mcuCommandUid"],))
        permit = replace(case.permit, permit_uid="99999999-9999-4999-8999-999999999999") if corruption == "permit" else case.permit
        with pytest.raises(ValueError):
            reporter.prepare(permit, case.start["mcuCommandUid"])


@pytest.mark.parametrize("price", [0, -1, True, 4294967296])
def test_invalid_original_price_never_creates_a_business_report(runtime, tmp_path, price):
    def invalid(command, start, config):
        command = original_command(command, start, config)
        command["payload"]["unitPriceTenThousandths"] = price
        command["payloadSha256"] = canonical_payload_sha256(command["payload"])
        return command
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=invalid) as case:
        RecoveryWire(case, runtime).finish_delivery()
        from native_result_report import NativeResultReporter
        with pytest.raises(ValueError):
            NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(
                case.permit, case.start["mcuCommandUid"])
        assert case.store.list_pending_events() == []
        assert case.store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"


@pytest.mark.parametrize("old_baseline", [80, None, 5000])
def test_actual_clean_result_reports_before_minus_after_and_new_baseline(runtime, tmp_path, old_baseline):
    def original(command, start, config):
        command = original_command(command, start, config)
        command["payload"]["oldBaselineWeightGrams"] = old_baseline
        command["payloadSha256"] = canonical_payload_sha256(command["payload"])
        return command
    with executed_action_case(runtime, tmp_path, clean_work=True, cloud_command_factory=original) as case:
        wire = case.wire = RecoveryWire(case, runtime)
        wire.finish_clean()
        from native_result_report import NativeResultReporter
        before = len(wire.sent)
        created = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(
            case.permit, case.start["mcuCommandUid"])
        assert created["state"] == "REPORT_CREATED"
        event = json.loads(case.store.get_event(created["eventUid"])["payload_json"])
        assert event["eventType"] == "CLEAN_COMPLETE"
        payload = event["payload"]
        assert payload["removedNetWeightGrams"] == 400
        assert payload["newBaselineWeightGrams"] == 100
        assert payload["oldBagUid"] == "88888888-8888-4888-8888-888888888888"
        assert payload["newBagUid"] == "99999999-9999-4999-8999-999999999999"
        assert payload["cleanerCompletionConfirmed"] is True
        assert payload["cleanLockAndManualDoorConfirmation"] == dict(lockPowerState="DEENERGIZED",
            solenoidHealth="UNKNOWN", physicalDoorStateBasis="CLEANER_CONFIRMATION", cleanerPhysicalCloseConfirmed=True)
        validate_event(event)
        assert encode_event_post("CLEAN_COMPLETE", event)
        assert case.store.get_work_slot() == case.occupancy
        assert len(wire.sent) == before
