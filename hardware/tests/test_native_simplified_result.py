"""Complete results suffice: real SQLite/report/permit code, no process proofs.

Wire inputs here are simulated; the MCU execution suite covers their producer.
No serial device, cloud service or physical actuator is opened.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
import uuid

import pytest

from edge_store import EdgeStore
from mcu_configuration import NativeMcuConfiguration
from native_result_report import NativeResultReporter
import uart2_protocol as uart
from hardware.tests.test_command_processor import make_real_job_safety
from hardware.tests.test_job_safety import _command
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_mcu_work_preparation import start_values
from hardware.tests.test_native_result_handoff import result_payload
from hardware.tests.test_native_result_report import original_command, validate_event


IDENTITY = {"mcuCommandUid", "targetMcuBootId", "commandSequence", "commandDigestSha256"}


def bind_boot(store):
    probe = store.reserve_native_query_id()
    boot = store.reserve_native_boot_id(probe)
    assert store.save_native_boot_observation("BIND_BOOT_REPLY", uart.encode_payload("BIND_BOOT_REPLY",
        dict(probeId=probe, proposedMcuBootId=boot, mcuBootId=boot, status="BOUND")))
    return boot


@contextmanager
def original_work(tmp_path, *, clean=False):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    updater, safety = make_real_job_safety(tmp_path)
    try:
        boot = bind_boot(store)
        config = NativeMcuConfiguration(**inputs())
        application = str(uuid.uuid4())
        for index in range(1, config.part_count + 1):
            name, raw = config.encode_part(index, application_uid=application,
                mcu_command_uid=str(uuid.uuid4()),
                target_mcu_boot_id=boot, command_sequence=index)
            value = uart.decode_payload(name, raw)
            record = store.prepare_native_command(name, value["mcuCommandUid"], boot,
                {key: field for key, field in value.items() if key not in IDENTITY})
            assert store.claim_native_command_write(record["command_uid"])
            assert store.save_native_command_observation("COMMAND_DECISION", uart.encode_payload("COMMAND_DECISION",
                {key: value[key] for key in IDENTITY} | dict(currentMcuBootId=boot, outcome="ACCEPTED", errorCode="NONE")))
        name = "START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION"
        key = "operationUid" if clean else "sessionUid"
        start = start_values(name, targetMcuBootId=boot, mcuCommandUid=str(uuid.uuid4()), **{key: str(uuid.uuid4())})
        command = _command() | dict(commandType=name, payload={key: start[key], "portNo": 1})
        command = original_command(command, start, inputs())
        assert store.receive_command(command["commandUid"], name, command) == "ACCEPTED"
        assert store.claim_next_command()
        permit = safety.request_job(command, work_type="CLEAN" if clean else "DELIVERY", work_uid=start[key])
        safety.begin_job(permit, begin_uid=permit.work_uid, digest=permit.request_digest_sha256)
        assert store.acquire_work_slot(permit.work_type, permit.work_uid, 1, {
            "phase": "NATIVE_RUNNING",
            "job_safety": asdict(permit) | {"begin_uid": permit.work_uid},
        })
        record = store.prepare_native_command(name, start["mcuCommandUid"], boot,
            {key: value for key, value in start.items() if key not in IDENTITY})
        assert store.claim_native_command_write(record["command_uid"])
        # Deliberately NO START reply, process records, second permission or GPIO evidence.
        value = dict(mcuBootId=boot, resultSequence=1, workUid=permit.work_uid,
            workType="CLEAN_OPERATION" if clean else "DELIVERY_SESSION", configVersion=start["configVersion"],
            originCommandUid=record["command_uid"], originCommandSequence=record["command_sequence"],
            completedUptimeMs=15000, deliveryRoundCount=0 if clean else 1, cleanActionSequence=1 if clean else 0,
            finishReason="CLEAN_CONFIRMED" if clean else "DELIVERY_END", physicalCloseConfirmed=clean,
            negativeWeightAnomaly=False, initialSourceMcuBootId=boot, finalSourceMcuBootId=boot,
            initialWeightGrams=500, finalWeightGrams=100 if clean else 700,
            initialCalibrationVersion=inputs()["ports"][0]["calibrationVersion"],
            finalCalibrationVersion=inputs()["ports"][0]["calibrationVersion"])
        yield SimpleNamespace(store=store, updater=updater, safety=safety, permit=permit, start=start,
            raw=result_payload(**value), reporter=NativeResultReporter(store, safety, device_name="device-1"))
    finally:
        store.close()
        updater.close()


@pytest.mark.parametrize("clean", [False, True])
def test_full_result_reports_without_any_process_or_action_proofs(tmp_path, clean):
    with original_work(tmp_path, clean=clean) as case:
        slot = case.store.get_work_slot()
        case.store.save_native_mcu_result(case.raw)
        report = case.reporter.prepare(case.permit, case.start["mcuCommandUid"])
        assert report["state"] == "REPORT_CREATED"
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        validate_event(event)
        payload = event["payload"]
        assert payload["removedNetWeightGrams" if clean else "deliveryNetWeightGrams"] == (400 if clean else 200)
        if clean:
            assert payload["cleanLockAndManualDoorConfirmation"]["solenoidHealth"] == "UNKNOWN"
        else:
            assert payload["finalDoorCommand"] == dict(command="CLOSE", outputStatus="COMMAND_DISPATCHED",
                physicalStateBasis="NOT_OBSERVABLE")
        assert case.store.get_work_slot() == slot  # Report creation alone is not admission.
        assert not case.store.list_native_work_actuator_events(case.permit.work_uid)
        case.store.close()
        case.store.initialize()
        assert case.reporter.prepare(case.permit, case.start["mcuCommandUid"]) == report
        assert len(case.store.list_pending_events()) == 1


def test_terminal_timeout_is_failure_not_zero_or_automatic_success(tmp_path):
    with original_work(tmp_path) as case:
        value = uart.decode_payload("WORK_RESULT", case.raw)
        value.update(finishReason="FAILED", finalKind="UNAVAILABLE", finalWeightGrams=0,
            finalElapsedMs=5000, finalSampleCount=0, finalSpanGrams=0, finalFaultCode="WEIGHT_TIMEOUT")
        value["resultDigestSha256"] = uart.compute_result_digest(value)
        case.store.save_native_mcu_result(uart.encode_payload("WORK_RESULT", value))
        report = case.reporter.prepare(case.permit, case.start["mcuCommandUid"])
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        validate_event(event)
        assert event["payload"]["completionReason"] == "TERMINAL_WEIGHT_FAILURE"
        assert event["payload"]["deliveryNetWeightGrams"] is None
        assert event["payload"]["finalPostCloseMeasurement"]["reportedWeightGrams"] is None


def test_result_still_has_to_belong_to_original_start(tmp_path):
    with original_work(tmp_path) as case:
        value = uart.decode_payload("WORK_RESULT", case.raw)
        value["originCommandUid"] = str(uuid.uuid4())
        value["resultDigestSha256"] = uart.compute_result_digest(value)
        case.store.save_native_mcu_result(uart.encode_payload("WORK_RESULT", value))
        with pytest.raises(ValueError, match="original START"):
            case.reporter.prepare(case.permit, case.start["mcuCommandUid"])
        assert not case.store.list_pending_events()


def test_storage_entry_cannot_freeze_a_report_under_an_unissued_permit(tmp_path):
    with original_work(tmp_path) as case:
        case.store.save_native_mcu_result(case.raw)
        snapshot = case.safety.get_job_permit(case.permit.permit_uid)
        for permit, original in ((replace(case.permit, permit_uid=str(uuid.uuid4())), snapshot),
                                 (case.permit, None), (case.permit, snapshot | {"state": "GRANTED"})):
            with pytest.raises(ValueError, match="original active job permit"):
                case.store.create_native_result_report(permit, case.start["mcuCommandUid"],
                    device_name="device-1", permit_snapshot=original)
        assert not case.store.list_pending_events()
        assert case.reporter.prepare(case.permit, case.start["mcuCommandUid"])["state"] == "REPORT_CREATED"


def test_saved_result_wins_before_confirmed_reboot_archive(tmp_path):
    with original_work(tmp_path) as case:
        case.store.save_native_mcu_result(case.raw)
        boot = bind_boot(case.store)
        decision = case.store.evaluate_native_work_recovery(case.permit, case.start["mcuCommandUid"], current_boot=lambda: boot)
        assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
        assert decision["evidence"]["state"] == "MATCHED"
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None


def test_legacy_clean_missing_weight_remains_a_complete_packet_not_a_normal_v2_report(tmp_path):
    with original_work(tmp_path, clean=True) as case:
        value = uart.decode_payload("WORK_RESULT", case.raw)
        value.update(finalKind="UNAVAILABLE", finalWeightGrams=0, finalElapsedMs=5000,
            finalSampleCount=0, finalSpanGrams=0, finalFaultCode="WEIGHT_TIMEOUT")
        value["resultDigestSha256"] = uart.compute_result_digest(value)
        case.store.save_native_mcu_result(uart.encode_payload("WORK_RESULT", value))
        boot = bind_boot(case.store)
        decision = case.store.evaluate_native_work_recovery(case.permit, case.start["mcuCommandUid"], current_boot=lambda: boot)
        assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
        assert case.reporter.prepare(case.permit, case.start["mcuCommandUid"])["state"] == "WAITING_FOR_RESULT_POLICY"
        assert not case.store.list_pending_events()


def test_late_result_after_archive_only_adds_evidence(tmp_path):
    with original_work(tmp_path) as case:
        boot = bind_boot(case.store)
        case.store.archive_native_delivery_issue(case.permit, case.start["mcuCommandUid"],
            device_name="device-1", current_boot=lambda: boot)
        original = case.store.get_native_delivery_issue(case.permit.work_uid)
        case.store.save_native_mcu_result(case.raw)
        report = case.reporter.prepare(case.permit, case.start["mcuCommandUid"])
        assert report["state"] == "DELIVERY_ISSUE_ARCHIVED"
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == original
        assert all(row["event_type"] != "DELIVERY_COMPLETE" for row in case.store.list_pending_events())
        assert case.store.get_native_mcu_result(1, 1)["payload"] == case.raw
