"""Native normal-result closing against real SQLite, confirmation and job stores.

Packet fixtures isolate closing failure policy; the last vertical tests also
run the real C producer through result custody, confirmation and completion.
No live cloud, serial port or actuator is used.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
import sqlite3
import uuid

import pytest

from native_business_completion import MARKER, NativeBusinessCompleter
from native_result_report import NativeResultReporter
from mcu_result_handoff import McuResultHandoff
from hardware.tests.test_mcu_simplified_execution import library, runtime, tick, select, request
from hardware.tests.test_mcu_work_preparation import take_samples
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_native_simplified_result import original_work, bind_boot
from hardware.tests.test_native_result_confirmation import confirmation_wire
from hardware.tests.test_simplified_mcu_pi_business import (
    real_work, restored_work_query, finish_delivery_round, no_process_evidence,
)
import uart2_protocol as uart


@contextmanager
def reported(tmp_path, *, clean=False, rounds=1, confirmation=True, quarantined=False):
    with original_work(tmp_path, clean=clean) as case:
        case.clean = clean
        if not clean and rounds != 1:
            value = uart.decode_payload("WORK_RESULT", case.raw)
            value["deliveryRoundCount"] = rounds
            value["resultDigestSha256"] = uart.compute_result_digest(value)
            case.raw = uart.encode_payload("WORK_RESULT", value)
        case.store.save_native_mcu_result(case.raw)
        case.report = case.reporter.prepare(case.permit, case.start["mcuCommandUid"])
        assert case.report["state"] == "REPORT_CREATED"
        if confirmation:
            _, _, command = confirmation_wire(case, case.report["eventUid"], quarantined=quarantined)
            assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        case.completer = NativeBusinessCompleter(case.store, case.safety, device_name="device-1")
        yield case


def complete(case):
    return case.completer.complete(case.permit, case.start["mcuCommandUid"])


def event_count(case):
    return case.store._conn.execute("SELECT COUNT(*) FROM event_outbox").fetchone()[0]


def put_baseline(store, bag_uid, *, source_work_uid=None, grams=99):
    value = dict(bag_uid=bag_uid, weight_grams=grams, source_kind="MANUAL_TEST",
        source_work_type="CLEAN_OPERATION", source_work_uid=source_work_uid or str(uuid.uuid4()),
        source_mcu_boot_id=77, source_mcu_event_sequence=100, source_observed_at=None,
        measurement_uid=str(uuid.uuid4()), updated_at="2026-09-13T01:00:00Z")
    with store.transaction() as conn:
        store._upsert_bag_baseline_in_tx(conn, value)
    return store.get_bag_baseline(bag_uid)


@pytest.mark.parametrize("clean,rounds", [(False, 1), (False, 2), (True, 0)])
def test_normal_business_finishes_permanent_job_before_releasing_slot_and_never_creates_another_event(tmp_path, clean, rounds):
    with reported(tmp_path, clean=clean, rounds=rounds) as case:
        count = event_count(case)
        event = json.loads(case.store.get_event(case.report["eventUid"])["payload_json"])
        new_bag = event["payload"].get("newBagUid")
        old_baseline = put_baseline(case.store, event["payload"]["oldBagUid"], grams=80) if clean else None
        original = case.safety.complete_job
        calls = []
        def checked(*args, **kwargs):
            marker = case.store.get_command(case.permit.command_uid)["result"][MARKER]
            assert marker["state"] == "PREPARED"
            assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
            if clean:
                assert case.store.get_bag_baseline(new_bag) is None
            calls.append(kwargs)
            return original(*args, **kwargs)
        case.safety.complete_job = checked
        result = complete(case)
        assert result["state"] == "COMPLETED"
        assert len(calls) == 1 and calls[0]["completion_uid"] == case.permit.command_uid
        assert calls[0]["outcome"] == "SUCCEEDED"
        assert result["evidenceSha256"] == calls[0]["completion_digest_sha256"]
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "COMPLETED"
        assert case.store.get_work_slot() is None
        assert case.store.get_command(case.permit.command_uid)["state"] == "COMPLETED"
        assert case.store.get_command(case.permit.command_uid)["result"][MARKER]["state"] == "APPLIED"
        if clean:
            baseline = case.store.get_bag_baseline(new_bag)
            assert baseline["weight_grams"] == 100
            assert baseline["source_work_uid"] == case.permit.work_uid
            assert baseline["measurement_uid"] == event["payload"]["cleanerConfirmedFinalMeasurement"]["measurementUid"]
            assert case.store.get_bag_baseline(event["payload"]["oldBagUid"]) == old_baseline
        else:
            assert case.store._conn.execute("SELECT COUNT(*) FROM bag_baseline").fetchone()[0] == 0
        assert event_count(case) == count
        case.store.close()
        case.store.initialize()
        assert complete(case) == result
        assert len(calls) == 1 and event_count(case) == count
        assert NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(
            case.permit, case.start["mcuCommandUid"]) == case.report


@pytest.mark.parametrize("quarantined", [False, True])
def test_platform_receipt_or_backend_quarantine_is_not_normal_completion(tmp_path, quarantined):
    with reported(tmp_path, confirmation=quarantined, quarantined=quarantined) as case:
        before = case.store.get_work_slot()
        event = case.store.get_event(case.report["eventUid"])
        if not quarantined:
            case.store.record_event_platform_reply(event["edge_event_sequence"], 200)
        result = complete(case)
        assert result["state"] == ("BACKEND_RESULT_NOT_APPLIED" if quarantined else "WAITING_FOR_BACKEND_CONFIRMATION")
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        assert case.store.get_work_slot() == before
        assert case.store.get_command(case.permit.command_uid)["result"] is None


def test_missing_final_weight_report_does_not_enter_normal_completion(tmp_path):
    with original_work(tmp_path) as case:
        value = uart.decode_payload("WORK_RESULT", case.raw)
        value.update(finishReason="FAILED", finalKind="UNAVAILABLE", finalWeightGrams=0,
            finalElapsedMs=5000, finalSampleCount=0, finalSpanGrams=0, finalFaultCode="WEIGHT_TIMEOUT")
        value["resultDigestSha256"] = uart.compute_result_digest(value)
        case.store.save_native_mcu_result(uart.encode_payload("WORK_RESULT", value))
        assert case.reporter.prepare(case.permit, case.start["mcuCommandUid"])["state"] == "REPORT_CREATED"
        completer = NativeBusinessCompleter(case.store, case.safety, device_name="device-1")
        assert completer.complete(case.permit, case.start["mcuCommandUid"])["state"] == "RESULT_NOT_NORMAL"
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def test_archived_delivery_with_late_complete_packet_cannot_close_as_success(tmp_path):
    with original_work(tmp_path) as case:
        boot = bind_boot(case.store)
        case.store.archive_native_delivery_issue(case.permit, case.start["mcuCommandUid"],
            device_name="device-1", current_boot=lambda: boot)
        case.store.save_native_mcu_result(case.raw)
        completer = NativeBusinessCompleter(case.store, case.safety, device_name="device-1")
        assert completer.complete(case.permit, case.start["mcuCommandUid"])["state"] == "ISSUE_ARCHIVED"
        assert case.store.get_command(case.permit.command_uid)["result"] is None
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


@pytest.mark.parametrize("applied_before_error", [False, True])
def test_permanent_completion_timeout_keeps_slot_and_restarts_with_identical_receipt(tmp_path, applied_before_error):
    with reported(tmp_path, clean=True) as case:
        original = case.safety.complete_job
        attempted = []
        def uncertain(*args, **kwargs):
            attempted.append(kwargs)
            if applied_before_error:
                original(*args, **kwargs)
            raise OSError("injected lost permanent completion reply")
        case.safety.complete_job = uncertain
        with pytest.raises(OSError, match="injected"):
            complete(case)
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.store._conn.execute("SELECT COUNT(*) FROM bag_baseline").fetchone()[0] == 0
        marker = case.store.get_command(case.permit.command_uid)["result"][MARKER]
        assert marker["state"] == "PREPARED"
        count = event_count(case)
        case.store.close()
        case.store.initialize()
        def retry(*args, **kwargs):
            assert kwargs == attempted[0]
            return original(*args, **kwargs)
        case.safety.complete_job = retry
        assert complete(case)["state"] == "COMPLETED"
        assert case.store.get_work_slot() is None and event_count(case) == count


def test_local_application_rolls_back_baseline_and_receipt_together_then_retries(tmp_path):
    with reported(tmp_path, clean=True) as case:
        with case.store.transaction() as conn:
            conn.execute("""CREATE TRIGGER fail_release BEFORE UPDATE ON work_slot
                WHEN NEW.work_type='NONE' BEGIN SELECT RAISE(ABORT, 'injected completion commit failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            complete(case)
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "COMPLETED"
        assert case.store.get_command(case.permit.command_uid)["result"][MARKER]["state"] == "PREPARED"
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.store._conn.execute("SELECT COUNT(*) FROM bag_baseline").fetchone()[0] == 0
        with case.store.transaction() as conn:
            conn.execute("DROP TRIGGER fail_release")
        case.store.close()
        case.store.initialize()
        assert complete(case)["state"] == "COMPLETED"


def test_conflicting_existing_baseline_stops_before_permanent_completion(tmp_path):
    with reported(tmp_path, clean=True) as case:
        event = json.loads(case.store.get_event(case.report["eventUid"])["payload_json"])
        other = put_baseline(case.store, event["payload"]["newBagUid"])
        with pytest.raises(ValueError, match="conflicting bag baseline"):
            complete(case)
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        assert case.store.get_bag_baseline(other["bag_uid"]) == other
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid


def test_completed_old_retry_never_changes_a_new_work_slot_or_newer_baseline(tmp_path):
    with reported(tmp_path, clean=True) as case:
        result = complete(case)
        event = json.loads(case.store.get_event(case.report["eventUid"])["payload_json"])
        new_uid = str(uuid.uuid4())
        assert case.store.acquire_work_slot("DELIVERY", new_uid, 1, {"phase": "NATIVE_RUNNING"})
        newer = put_baseline(case.store, event["payload"]["newBagUid"], source_work_uid=new_uid, grams=456)
        slot = case.store.get_work_slot()
        case.store.close()
        case.store.initialize()
        assert complete(case) == result
        assert case.store.get_work_slot() == slot
        assert case.store.get_bag_baseline(newer["bag_uid"]) == newer


def test_replaced_slot_between_permanent_rpc_and_apply_is_not_released(tmp_path):
    with reported(tmp_path, clean=True) as case:
        original = case.safety.complete_job
        new_uid = str(uuid.uuid4())
        def replace_after_rpc(*args, **kwargs):
            original(*args, **kwargs)
            assert case.store.release_work_slot(case.permit.work_uid)
            assert case.store.acquire_work_slot("DELIVERY", new_uid, 1, {"phase": "NATIVE_RUNNING"})
        case.safety.complete_job = replace_after_rpc
        with pytest.raises(ValueError, match="work slot has changed"):
            complete(case)
        assert case.store.get_work_slot()["work_uid"] == new_uid
        assert case.store._conn.execute("SELECT COUNT(*) FROM bag_baseline").fetchone()[0] == 0
        assert case.store.get_command(case.permit.command_uid)["result"][MARKER]["state"] == "PREPARED"


def test_wrong_permanent_completion_identity_cannot_release_the_original_slot(tmp_path):
    with reported(tmp_path) as case:
        original = case.safety.complete_job
        def wrong(*args, **kwargs):
            return original(*args, **(kwargs | {"completion_uid": str(uuid.uuid4())}))
        case.safety.complete_job = wrong
        with pytest.raises(ValueError, match="exact completed permanent receipt"):
            complete(case)
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.store.get_command(case.permit.command_uid)["result"][MARKER]["state"] == "PREPARED"


def test_corrupted_prepared_marker_is_not_repaired_from_current_state(tmp_path):
    with reported(tmp_path) as case:
        case.store.prepare_native_business_completion(case.permit, case.start["mcuCommandUid"], device_name="device-1",
            permit_snapshot=case.safety.get_job_permit(case.permit.permit_uid))
        value = case.store.get_command(case.permit.command_uid)["result"]
        value[MARKER]["evidenceSha256"] = "f" * 64
        with case.store.transaction() as conn:
            conn.execute("UPDATE command_inbox SET result_json=? WHERE command_uid=?",
                (json.dumps(value), case.permit.command_uid))
        with pytest.raises(ValueError, match="receipt is corrupt"):
            complete(case)
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def test_local_preparation_failure_rolls_back_original_command_and_slot_together(tmp_path):
    with reported(tmp_path) as case:
        before = case.store.get_work_slot()
        with case.store.transaction() as conn:
            conn.execute("""CREATE TRIGGER fail_prepare BEFORE UPDATE ON work_slot
                WHEN NEW.work_state='COMPLETING' BEGIN SELECT RAISE(ABORT, 'injected preparation failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            complete(case)
        assert case.store.get_command(case.permit.command_uid)["state"] == "PROCESSING"
        assert case.store.get_command(case.permit.command_uid)["result"] is None
        assert case.store.get_work_slot() == before
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def test_local_apply_cannot_release_while_permanent_permit_is_still_active(tmp_path):
    with reported(tmp_path) as case:
        snapshot = case.safety.get_job_permit(case.permit.permit_uid)
        case.store.prepare_native_business_completion(case.permit, case.start["mcuCommandUid"],
            device_name="device-1", permit_snapshot=snapshot)
        with pytest.raises(ValueError, match="exact completed permanent receipt"):
            case.store.apply_native_business_completion(case.permit, case.start["mcuCommandUid"],
                device_name="device-1", permit_snapshot=snapshot)
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.store.get_command(case.permit.command_uid)["result"][MARKER]["state"] == "PREPARED"


def test_conflicting_baseline_created_during_rpc_is_not_overwritten(tmp_path):
    with reported(tmp_path, clean=True) as case:
        original = case.safety.complete_job
        event = json.loads(case.store.get_event(case.report["eventUid"])["payload_json"])
        introduced = []
        def change_baseline(*args, **kwargs):
            original(*args, **kwargs)
            introduced.append(put_baseline(case.store, event["payload"]["newBagUid"], grams=999))
        case.safety.complete_job = change_baseline
        with pytest.raises(ValueError, match="conflicting bag baseline"):
            complete(case)
        assert case.store.get_bag_baseline(introduced[0]["bag_uid"]) == introduced[0]
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "COMPLETED"
        assert case.store.get_command(case.permit.command_uid)["result"][MARKER]["state"] == "PREPARED"
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid


@pytest.mark.parametrize("changed", ["permit", "start", "device"])
def test_another_identity_cannot_finish_the_original_report(tmp_path, changed):
    with reported(tmp_path) as case:
        permit = replace(case.permit, command_uid=str(uuid.uuid4())) if changed == "permit" else case.permit
        start = str(uuid.uuid4()) if changed == "start" else case.start["mcuCommandUid"]
        name = "another-device" if changed == "device" else "device-1"
        before = case.store.get_work_slot()
        with pytest.raises(ValueError):
            NativeBusinessCompleter(case.store, case.safety, device_name=name).complete(permit, start)
        assert case.store.get_work_slot() == before
        assert case.store.get_command(case.permit.command_uid)["result"] is None
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def test_empty_malformed_original_command_result_is_not_discarded(tmp_path):
    with reported(tmp_path) as case:
        with case.store.transaction() as conn:
            conn.execute("UPDATE command_inbox SET result_json='[]' WHERE command_uid=?", (case.permit.command_uid,))
        with pytest.raises(ValueError, match="command result is malformed"):
            complete(case)
        assert case.store.get_command(case.permit.command_uid)["result"] == []
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


@pytest.mark.parametrize("business", ["delivery", "continuous_delivery", "clean"])
def test_real_c_result_and_exact_backend_confirmation_complete_original_job_once(runtime, tmp_path, business):
    clean = business == "clean"
    with real_work(runtime, tmp_path, clean) as case:
        case.clean = clean
        now = case.wire.now
        if clean:
            now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
            assert request(runtime, case.cleanup, case.start, now, "CLEAN_FINISH_REQUESTED")
            now = take_samples(runtime, [100] * 5, start=now, measurement=2)
        else:
            now = finish_delivery_round(runtime, case, now, 2, 700)
            if business == "continuous_delivery":
                assert select(runtime, case.delivery, now, "CONTINUE")
                now = finish_delivery_round(runtime, case, now, 3, 900)
            assert select(runtime, case.delivery, now, "END")
        case.wire.now = now
        observation = restored_work_query(case, clean, 1000)
        assert observation["status"] == "RESULT_HELD"
        handoff = McuResultHandoff(case.store, case.wire.write, dict(mcuBootId=case.boot,
            resultSequence=observation["resultSequence"], resultDigestSha256=observation["resultDigestSha256"],
            workUid=case.permit.work_uid))
        assert handoff.poll(1000)
        assert case.wire.deliver(handoff, 1000) == [
            ("RESULT_QUERY_REPLY", True), ("WORK_RESULT", True), ("RESULT_SAVED_REPLY", False)]
        reporter = NativeResultReporter(case.store, case.safety, device_name="device-1")
        report = reporter.prepare(case.permit, case.start["mcuCommandUid"])
        _, _, command = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        count = event_count(case)
        before_writes = list(case.wire.sent)
        case.store.close()
        case.store.initialize()
        case.completer = NativeBusinessCompleter(case.store, case.safety, device_name="device-1")
        completed = complete(case)
        assert completed["state"] == "COMPLETED"
        assert completed["eventUid"] == report["eventUid"]
        assert case.store.get_work_slot() is None
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "COMPLETED"
        if clean:
            event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
            baseline = case.store.get_bag_baseline(event["payload"]["newBagUid"])
            assert baseline["weight_grams"] == 100 and baseline["source_work_uid"] == case.permit.work_uid
        case.store.close()
        case.store.initialize()
        assert complete(case) == completed
        assert event_count(case) == count
        assert case.wire.sent == before_writes  # Closing never sends a fresh START or physical action.
        no_process_evidence(case)
