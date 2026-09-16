"""Actual MCU result -> original cloud command -> atomic reliable report."""
import json
from pathlib import Path
import sqlite3
import uuid
from dataclasses import asdict, replace
import pytest

import uart2_protocol as uart
from hardware.tests.test_mcu_simplified_execution import library, runtime
from hardware.tests.native_autonomous_recovery_fixture import autonomous_active_case as executed_action_case
from hardware.tests.test_native_work_recovery import RecoveryWire
from onenet_wire import canonical_payload_sha256, encode_event_post
from contractlib import JsonSchemaSubsetValidator
from validate_contracts import _validate_event_semantics


def finish_with_samples(case, runtime, samples, *, window_expired=False):
    from hardware.tests.test_native_configuration import inputs
    from hardware.tests.test_mcu_simplified_execution import request, select
    from hardware.tests.test_mcu_work_preparation import take_samples
    wire = case.wire
    if case.clean:
        assert request(runtime, case.cleanup, case.start, wire.now, "CLEAN_FINISH_REQUESTED")
    else:
        wire.advance(inputs()["device"]["deliveryDoorTravelWaitMs"])
    began = wire.now
    wire.now = take_samples(runtime, samples, start=began, measurement=2)
    if len(samples) != 5:
        wire.advance(began + 5000 - wire.now)
    # rc.23 clean freezes after FINISH + final measurement. Delivery still
    # needs the local END/window choice owned by the MCU state machine.
    if not case.clean and samples:
        if window_expired:
            wire.advance(case.start["continueDeliveryWaitMs"])
        else:
            assert select(runtime, case.delivery, wire.now, "END")
    wire.advance(0)
    return wire.handoff_result()


@pytest.mark.parametrize("clean", [False, True])
def test_multiple_rounds_report_the_original_first_and_current_final_not_intermediate_weight(runtime, tmp_path, clean):
    from hardware.tests.test_mcu_simplified_execution import request, select, tick
    from hardware.tests.test_mcu_work_preparation import take_samples
    from hardware.tests.test_native_configuration import inputs
    from hardware.tests.test_simplified_mcu_pi_business import finish_delivery_round
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=clean, cloud_command_factory=original_command) as case:
        if clean:
            now = case.wire.now
            assert request(runtime, case.cleanup, case.start, now, "CLEAN_UNLOCK_REQUESTED")
            now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
            assert request(runtime, case.cleanup, case.start, now, "CLEAN_FINISH_REQUESTED")
            case.wire.now = take_samples(runtime, [900] * 5, start=now, measurement=2)
            case.wire.handoff_result()
        else:
            now = tick(runtime, case.wire.now, inputs()["device"]["deliveryDoorTravelWaitMs"])
            now = take_samples(runtime, [700] * 5, start=now, measurement=2)
            assert select(runtime, case.delivery, now, "CONTINUE")
            now = finish_delivery_round(runtime, case, now, 3, 900)
            assert select(runtime, case.delivery, now, "END")
            case.wire.now = now
            case.wire.handoff_result()
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        validate_event(event)
        payload = event["payload"]
        if clean:
            assert payload["removedNetWeightGrams"] == -400
            assert payload["newBaselineWeightGrams"] == 900
            assert payload["cleanActionSequence"] == 2
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


def test_clean_final_timeout_stays_local_as_failed_result_without_normal_completion(runtime, tmp_path):
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=True, cloud_command_factory=original_command) as case:
        saved = finish_with_samples(case, runtime, [])
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["physicalCloseConfirmed"] and result["finalKind"] == "UNAVAILABLE"
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        assert report["state"] != "REPORT_CREATED"
        assert case.store.list_pending_events() == []
        assert case.store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
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


def test_timeout_result_itself_proves_last_close_control_without_old_action_evidence(runtime, tmp_path):
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        finish_with_samples(case, runtime, [])
        assert case.store.list_native_work_actuator_events(case.permit.work_uid) == []
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        assert report["state"] == "REPORT_CREATED"
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        assert event["payload"]["finalDoorCommand"] == dict(command="CLOSE",
            outputStatus="COMMAND_DISPATCHED", physicalStateBasis="NOT_OBSERVABLE")
        names = {row["message_name"] for row in case.store.list_native_commands()}
        assert names.isdisjoint({"AUTHORIZE_DELIVERY_FIRST_OPEN", "UNLOCK_CLEAN_DOOR"})


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


def install_fullness_configuration(case, *, mode="SENSOR_ONLY",
                                   kind="DIGITAL_INFRARED"):
    from hardware.tests.test_native_configuration import inputs

    original = case.store.get_command(case.permit.command_uid)["payload"]
    identity = original["payload"]["config"]
    port = dict(inputs()["ports"][0])
    port.update(
        fullnessMode=mode,
        fullnessSensorKind=kind,
        configuredFullWeightGrams=100,
        fullnessDistanceThresholdMm=600,
    )
    payload = {"config": identity, "ports": [port]}
    with case.store.transaction() as conn:
        conn.execute(
            """INSERT INTO configuration_state
               (application_uid, command_uid, device_name, config_version,
                content_sha256, mcu_payload_sha256, payload_json,
                part_command_uids_json, state, edge_saved_at, applied_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, '[]', 'APPLIED', ?, ?)""",
            (
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                "device-1",
                identity["version"],
                identity["contentSha256"],
                identity["mcuPayloadSha256"],
                json.dumps(payload),
                "2026-09-16T00:00:00.000Z",
                "2026-09-16T00:00:00.000Z",
            ),
        )
    return port


def native_device_facts(*, blocked=True):
    return {
        "status": "AVAILABLE",
        "portNo": 1,
        "currentMcuBootId": 1,
        "capturedUptimeMs": 4_000_000,
        "fullnessObservationKind": "DIGITAL_INFRARED",
        "fullnessReadStatus": "VALID",
        # Old auxiliary observations remain usable; no freshness gate applies.
        "fullnessCapturedUptimeMs": 1,
        "fullnessInfraredBlocked": blocked,
        "fullnessDistanceMm": 0,
    }


@pytest.mark.parametrize("clean", [False, True])
def test_native_report_atomically_projects_current_or_new_bag_fullness(
    runtime,
    tmp_path,
    clean,
):
    from native_result_report import NativeResultReporter

    with executed_action_case(
        runtime,
        tmp_path,
        clean_work=clean,
        cloud_command_factory=original_command,
    ) as case:
        saved = (
            RecoveryWire(case, runtime).finish_clean()
            if clean
            else RecoveryWire(case, runtime).finish_delivery()
        )
        install_fullness_configuration(case)
        reads = []

        def facts():
            reads.append(True)
            return native_device_facts()

        reporter = NativeResultReporter(
            case.store,
            case.safety,
            device_name="device-1",
            device_facts_provider=facts,
        )
        report = reporter.prepare(
            case.permit,
            case.start["mcuCommandUid"],
        )
        assert report["state"] == "REPORT_CREATED"
        events = case.store.list_pending_events(10)
        assert [event["event_type"] for event in events] == [
            "CLEAN_COMPLETE" if clean else "DELIVERY_COMPLETE",
            "FULLNESS_STATE_CHANGED",
        ]
        completion = json.loads(events[0]["payload_json"])
        fullness = json.loads(events[1]["payload_json"])
        validate_event(completion)
        validate_event(fullness)
        command = case.store.get_command(case.permit.command_uid)["payload"]
        expected_bag = command["payload"][
            "newBagUid" if clean else "bagUid"
        ]
        final = completion["payload"][
            "cleanerConfirmedFinalMeasurement"
            if clean
            else "finalPostCloseMeasurement"
        ]
        assert fullness["payload"]["bagUid"] == expected_bag
        assert fullness["payload"]["state"] == "FULL"
        assert fullness["payload"]["confirmationBasis"] == (
            "MCU_INDEPENDENT_RECHECK"
        )
        assert fullness["payload"]["fullnessSensorValue"] == "BLOCKED"
        assert fullness["payload"]["totalWeightMeasurement"] == final
        assert fullness["payload"]["baselineWeightGrams"] == (
            final["reportedWeightGrams"] if clean else None
        )
        assert case.store.get_port_fullness_state(1, expected_bag) == "FULL"
        if clean:
            # The local fullness calculation uses the new tare immediately,
            # while formal baseline application still waits for backend
            # confirmation in NativeBusinessCompleter.
            assert case.store.get_bag_baseline(expected_bag) is None

        assert reporter.prepare(
            case.permit,
            case.start["mcuCommandUid"],
        ) == report
        assert len(reads) == 1
        assert case.store.save_native_mcu_result(saved["payload"])[
            "taskUid"
        ] == report["eventUid"]
        assert reporter.prepare(
            case.permit,
            case.start["mcuCommandUid"],
        ) == report
        assert len(reads) == 1

        case.store.close()
        case.store.initialize()

        def unexpected_read():
            raise AssertionError("existing report re-read DEVICE_FACTS")

        restarted = NativeResultReporter(
            case.store,
            case.safety,
            device_name="device-1",
            device_facts_provider=unexpected_read,
        )
        assert restarted.prepare(
            case.permit,
            case.start["mcuCommandUid"],
        ) == report
        assert len(
            [
                event
                for event in case.store.list_pending_events(10)
                if event["event_type"] == "FULLNESS_STATE_CHANGED"
            ]
        ) == 1


@pytest.mark.parametrize(
    "failure_target,trigger_sql",
    [
        (
            "completion",
            """CREATE TRIGGER fail_native_completion
               BEFORE INSERT ON event_outbox
               WHEN NEW.event_type='DELIVERY_COMPLETE'
               BEGIN SELECT RAISE(ABORT, 'injected completion failure'); END""",
        ),
        (
            "fullness",
            """CREATE TRIGGER fail_native_fullness
               BEFORE INSERT ON port_fullness_state
               BEGIN SELECT RAISE(ABORT, 'injected fullness failure'); END""",
        ),
        (
            "report",
            """CREATE TRIGGER fail_native_report
               BEFORE UPDATE ON native_result_report_outbox
               WHEN NEW.state='REPORT_CREATED'
               BEGIN SELECT RAISE(ABORT, 'injected report failure'); END""",
        ),
    ],
)
def test_native_completion_fullness_and_report_state_roll_back_together(
    runtime,
    tmp_path,
    failure_target,
    trigger_sql,
):
    from native_result_report import NativeResultReporter

    with executed_action_case(
        runtime,
        tmp_path,
        clean_work=False,
        cloud_command_factory=original_command,
    ) as case:
        RecoveryWire(case, runtime).finish_delivery()
        install_fullness_configuration(case)
        reporter = NativeResultReporter(
            case.store,
            case.safety,
            device_name="device-1",
            device_facts_provider=native_device_facts,
        )
        with case.store.transaction() as conn:
            conn.execute(trigger_sql)

        with pytest.raises(
            sqlite3.IntegrityError,
            match=f"injected {failure_target} failure",
        ):
            reporter.prepare(case.permit, case.start["mcuCommandUid"])

        assert case.store.list_pending_events(10) == []
        assert case.store._conn.execute(
            "SELECT COUNT(*) FROM port_fullness_state"
        ).fetchone()[0] == 0
        task = case.store.list_native_result_report_tasks()[0]
        assert task["state"] == "PENDING_CLASSIFICATION"
        assert task["event_uid"] is None
        with case.store.transaction() as conn:
            conn.execute(f"DROP TRIGGER fail_native_{failure_target}")
        assert reporter.prepare(
            case.permit,
            case.start["mcuCommandUid"],
        )["state"] == "REPORT_CREATED"


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


@pytest.mark.parametrize("missing", ["cloud", "no_result"])
def test_missing_original_authority_or_result_stays_local_without_business_event(runtime, tmp_path, missing):
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=False,
            cloud_command_factory=None if missing == "cloud" else original_command) as case:
        if missing != "no_result":
            RecoveryWire(case, runtime).finish_delivery()
        if missing == "cloud":
            case.store._conn.execute("PRAGMA foreign_keys=OFF")
            with case.store.transaction() as conn:
                conn.execute("DELETE FROM command_inbox WHERE command_uid=?", (case.permit.command_uid,))
            case.store._conn.execute("PRAGMA foreign_keys=ON")
        state = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        assert state["state"] != "REPORT_CREATED"
        assert case.store.list_pending_events() == []
        names = {row["message_name"] for row in case.store.list_native_commands()}
        assert names.isdisjoint({"AUTHORIZE_DELIVERY_FIRST_OPEN", "UNLOCK_CLEAN_DOOR"})
        assert case.store.get_work_slot() == case.occupancy


def test_missing_optional_process_rows_do_not_block_current_normal_report(runtime, tmp_path):
    from native_result_report import NativeResultReporter
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        RecoveryWire(case, runtime).finish_delivery()
        case.store._conn.execute("PRAGMA foreign_keys=OFF")
        with case.store.transaction() as conn:
            conn.execute("DELETE FROM native_process_receipt")
            conn.execute("DELETE FROM native_measurement_event")
        case.store._conn.execute("PRAGMA foreign_keys=ON")
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(
            case.permit, case.start["mcuCommandUid"])
        assert report["state"] == "REPORT_CREATED"
        assert len(case.store.list_pending_events()) == 1


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
        assert CURRENT_SCHEMA_VERSION == 40
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


def replace_original_cloud_payload(case, update):
    """Rewrite a complete original authority set before report classification."""
    from job_safety import command_request_digest

    row = case.store.get_command(case.permit.command_uid)
    command = json.loads(json.dumps(row["payload"]))
    update(command["payload"])
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    digest = command_request_digest(command)
    raw = json.dumps(command, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    with case.store.transaction() as conn:
        conn.execute("UPDATE command_inbox SET payload_json=?,canonical_sha256=? WHERE command_uid=?",
            (raw, canonical_payload_sha256(command), case.permit.command_uid))
    permanent = case.safety._client.store._connection
    permanent.execute("UPDATE job_permit SET request_digest_sha256=?,permit_digest_sha256=? WHERE permit_uid=?",
        (digest, digest, case.permit.permit_uid))
    permanent.commit()
    case.permit = replace(case.permit, request_digest_sha256=digest)
    context = case.store.get_work_slot()["context"] | {
        "job_safety": asdict(case.permit) | {"begin_uid": case.permit.work_uid}}
    assert case.store.update_work_context(case.permit.work_uid, context)
    case.occupancy = case.store.get_work_slot()


@pytest.mark.parametrize("price", [0, -1, True, 4294967296])
def test_invalid_original_price_never_creates_a_business_report(runtime, tmp_path, price):
    with executed_action_case(runtime, tmp_path, clean_work=False, cloud_command_factory=original_command) as case:
        RecoveryWire(case, runtime).finish_delivery()
        replace_original_cloud_payload(case,
            lambda payload: payload.update(unitPriceTenThousandths=price))
        from native_result_report import NativeResultReporter
        with pytest.raises(ValueError):
            NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(
                case.permit, case.start["mcuCommandUid"])
        assert case.store.list_pending_events() == []
        assert case.store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"


@pytest.mark.parametrize("old_baseline", [80, None, 5000])
def test_actual_clean_result_reports_before_minus_after_and_new_baseline(runtime, tmp_path, old_baseline):
    with executed_action_case(runtime, tmp_path, clean_work=True, cloud_command_factory=original_command) as case:
        wire = case.wire = RecoveryWire(case, runtime)
        wire.finish_clean()
        replace_original_cloud_payload(case,
            lambda payload: payload.update(oldBaselineWeightGrams=old_baseline))
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
