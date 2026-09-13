"""Issue-only closing through real SQLite, issue confirmations and UpdaterStore.

The module under test never calls the updater: snapshots cross its boundary.
No cloud connection, serial device, physical action or normal order is created.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
import sqlite3
import uuid

import pytest

from native_delivery_issue_report import NativeDeliveryIssueReporter
from hardware.tests.test_native_simplified_result import original_work, bind_boot
from hardware.tests.test_native_delivery_issue_confirmation import issue_confirmation_wire
from hardware.tests.test_native_business_completion import put_baseline
import uart2_protocol as uart


@contextmanager
def archived(tmp_path, *, confirmation=True, quarantined=False, report=True):
    with original_work(tmp_path) as case:
        case.clean = False
        boot = bind_boot(case.store)
        decision = case.store.archive_native_delivery_issue(case.permit, case.start["mcuCommandUid"],
            device_name="device-1", current_boot=lambda: boot)
        case.issue = decision["issue"]
        if report:
            NativeDeliveryIssueReporter(case.store, device_name="device-1").prepare(case.permit.work_uid)
        if confirmation:
            _, _, command = issue_confirmation_wire(case, case.issue["issueUid"], quarantined=quarantined)
            assert case.store.receive_business_confirmation_and_create_receipt(
                command=command, device_name="device-1") == "ACCEPTED"
        yield case


def prepare(case):
    return case.store.prepare_native_issue_completion(case.permit, case.start["mcuCommandUid"],
        device_name="device-1", permit_snapshot=case.safety.get_job_permit(case.permit.permit_uid))


def apply(case):
    return case.store.apply_native_issue_completion(case.permit, case.start["mcuCommandUid"],
        device_name="device-1", permit_snapshot=case.safety.get_job_permit(case.permit.permit_uid))


def cancel_permanent(case, pending):
    case.safety.complete_job(case.permit, completion_uid=pending["completionUid"],
        outcome="CANCELLED", completion_digest_sha256=pending["evidenceSha256"])


def test_confirmed_issue_cancels_original_permit_before_releasing_only_original_slot(tmp_path):
    with archived(tmp_path) as case:
        before_events = case.store.list_pending_events()
        pending = prepare(case)
        assert pending["state"] == "PREPARED"
        assert pending["completionUid"] == case.permit.command_uid
        assert pending["completionOutcome"] == "CANCELLED"
        assert pending["issueUid"] == case.issue["issueUid"]
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.store.get_command(case.permit.command_uid)["state"] == "FAILED"
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        cancel_permanent(case, pending)
        finished = apply(case)
        assert finished == pending | {"state": "COMPLETED"}
        assert case.store.get_work_slot() is None
        assert case.store.get_command(case.permit.command_uid)["result"]["nativeIssueCompletion"]["state"] == "APPLIED"
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == case.issue
        assert case.store.list_pending_events() == before_events
        assert not case.store.list_native_work_actuator_events(case.permit.work_uid)


def test_an_existing_null_completion_marker_is_not_silently_replaced(tmp_path):
    with archived(tmp_path) as case:
        with case.store.transaction() as conn:
            conn.execute("UPDATE command_inbox SET result_json=? WHERE command_uid=?",
                (json.dumps({"nativeIssueCompletion": None}), case.permit.command_uid))
        with pytest.raises(ValueError, match="receipt is corrupt"):
            prepare(case)
        assert case.store.get_command(case.permit.command_uid)["result"] == {"nativeIssueCompletion": None}
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid


def test_changed_original_slot_receipt_during_permanent_completion_cannot_release_it(tmp_path):
    with archived(tmp_path) as case:
        pending = prepare(case)
        cancel_permanent(case, pending)
        context = case.store.get_work_slot()["context"]
        context["nativeIssueCompletion"]["evidenceSha256"] = "0" * 64
        case.store.update_work_context(case.permit.work_uid, context)
        with pytest.raises(ValueError, match="slot receipt"):
            apply(case)
        assert case.store.get_work_slot()["context"] == context
        assert case.store.get_command(case.permit.command_uid)["result"]["nativeIssueCompletion"]["state"] == "PREPARED"


@pytest.mark.parametrize("receipt", ["none", "platform", "quarantined", "evidence_only"])
def test_only_exact_archive_business_confirmation_can_prepare_cancellation(tmp_path, receipt):
    with archived(tmp_path, confirmation=receipt == "quarantined", quarantined=receipt == "quarantined") as case:
        if receipt == "platform":
            event = case.store.get_event(case.issue["issueUid"])
            case.store.record_event_platform_reply(event["edge_event_sequence"], 200)
        elif receipt == "evidence_only":
            part = next(row for row in case.store.list_native_delivery_issue_reports(case.permit.work_uid)
                if row["evidence_kind"] == "ARCHIVE_CONTEXT")
            _, _, command = issue_confirmation_wire(case, part["event_uid"])
            assert case.store.receive_business_confirmation_and_create_receipt(
                command=command, device_name="device-1") == "ACCEPTED"
        slot = case.store.get_work_slot()
        expected = "BACKEND_ISSUE_NOT_APPLIED" if receipt == "quarantined" else "WAITING_FOR_BACKEND_CONFIRMATION"
        assert prepare(case) == {"state": expected}
        assert apply(case) == {"state": expected}
        assert case.store.get_work_slot() == slot
        assert case.store.get_command(case.permit.command_uid)["result"] is None
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


@pytest.mark.parametrize("completed_before_restart", [False, True])
def test_restart_before_or_after_lost_permanent_reply_keeps_exact_cancel_identity(tmp_path, completed_before_restart):
    with archived(tmp_path) as case:
        pending = prepare(case)
        if completed_before_restart:
            cancel_permanent(case, pending)
        case.store.close()
        case.updater.close()
        case.store.initialize()
        case.updater.initialize()
        assert prepare(case) == pending
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        cancel_permanent(case, pending)  # Real UpdaterStore also checks the duplicate receipt.
        assert apply(case) == pending | {"state": "COMPLETED"}
        assert case.store.get_work_slot() is None


def test_old_retry_and_late_result_never_touch_new_slot_bag_or_admission_fault(tmp_path):
    with archived(tmp_path) as case:
        case.store.set_state("native_blocking_fault", "MCU_CONFIGURATION_NOT_APPLIED")
        pending = prepare(case)
        cancel_permanent(case, pending)
        finished = apply(case)
        new_uid, bag = str(uuid.uuid4()), str(uuid.uuid4())
        assert case.store.acquire_work_slot("CLEAN", new_uid, 1, {"phase": "NATIVE_RUNNING"})
        baseline = put_baseline(case.store, bag, source_work_uid=new_uid, grams=456)
        new_slot = case.store.get_work_slot()
        case.store.save_native_mcu_result(case.raw)
        events = case.store.list_pending_events()
        case.store.close()
        case.store.initialize()
        assert prepare(case) == apply(case) == finished
        assert case.store.get_work_slot() == new_slot
        assert case.store.get_bag_baseline(bag) == baseline
        assert case.store.get_state("native_blocking_fault") == "MCU_CONFIGURATION_NOT_APPLIED"
        assert case.store.list_pending_events() == events
        assert case.reporter.prepare(case.permit, case.start["mcuCommandUid"])["state"] == "DELIVERY_ISSUE_ARCHIVED"


@pytest.mark.parametrize("stage", ["prepare", "apply"])
def test_slot_write_failure_rolls_back_local_marker_and_command_together(tmp_path, stage):
    with archived(tmp_path) as case:
        if stage == "apply":
            pending = prepare(case)
            cancel_permanent(case, pending)
        command, slot = case.store.get_command(case.permit.command_uid), case.store.get_work_slot()
        with case.store.transaction() as conn:
            conn.execute("""CREATE TRIGGER fail_issue_slot BEFORE UPDATE ON work_slot
                BEGIN SELECT RAISE(ABORT, 'injected issue slot write failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            (prepare if stage == "prepare" else apply)(case)
        assert case.store.get_command(case.permit.command_uid) == command
        assert case.store.get_work_slot() == slot
        with case.store.transaction() as conn:
            conn.execute("DROP TRIGGER fail_issue_slot")
        case.store.close()
        case.store.initialize()
        pending = prepare(case)
        cancel_permanent(case, pending)
        assert apply(case)["state"] == "COMPLETED"


@pytest.mark.parametrize("changed", ["permit_uid", "command_uid", "request_digest_sha256", "work_type", "start", "device"])
def test_another_original_identity_cannot_close_an_issue(tmp_path, changed):
    with archived(tmp_path) as case:
        permit, start, device = case.permit, case.start["mcuCommandUid"], "device-1"
        if changed in {"permit_uid", "command_uid"}:
            permit = replace(permit, **{changed: str(uuid.uuid4())})
        elif changed == "request_digest_sha256":
            permit = replace(permit, request_digest_sha256="0" * 64)
        elif changed == "work_type":
            permit = replace(permit, work_type="CLEAN")
        elif changed == "start":
            start = str(uuid.uuid4())
        else:
            device = "another-device"
        slot = case.store.get_work_slot()
        snapshot = case.safety.get_job_permit(case.permit.permit_uid)
        with pytest.raises(ValueError):
            case.store.prepare_native_issue_completion(permit, start, device_name=device, permit_snapshot=snapshot)
        assert case.store.get_work_slot() == slot
        assert case.store.get_command(case.permit.command_uid)["result"] is None


@pytest.mark.parametrize("changed", ["completion_uid", "outcome", "completion_digest_sha256"])
def test_wrong_real_permanent_completion_cannot_release_issue_slot(tmp_path, changed):
    with archived(tmp_path) as case:
        pending = prepare(case)
        receipt = dict(completion_uid=pending["completionUid"], outcome="CANCELLED",
            completion_digest_sha256=pending["evidenceSha256"])
        receipt[changed] = str(uuid.uuid4()) if changed == "completion_uid" else "SUCCEEDED" if changed == "outcome" else "0" * 64
        case.safety.complete_job(case.permit, **receipt)
        for attempt in (prepare, apply):
            with pytest.raises(ValueError, match="exact cancelled permanent receipt"):
                attempt(case)
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.store.get_command(case.permit.command_uid)["result"]["nativeIssueCompletion"]["state"] == "PREPARED"


def test_active_permanent_snapshot_cannot_apply_and_completed_snapshot_cannot_first_prepare(tmp_path):
    with archived(tmp_path) as case:
        snapshot = case.safety.get_job_permit(case.permit.permit_uid)
        pending = prepare(case)
        with pytest.raises(ValueError, match="exact cancelled permanent receipt"):
            apply(case)
        assert case.safety.get_job_permit(case.permit.permit_uid) == snapshot
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
    other = tmp_path / "already-completed"
    other.mkdir()
    with archived(other) as case:
        case.safety.complete_job(case.permit, completion_uid=case.permit.command_uid,
            outcome="CANCELLED", completion_digest_sha256=pending["evidenceSha256"])
        with pytest.raises(ValueError, match="original active permanent permit"):
            prepare(case)
        assert case.store.get_command(case.permit.command_uid)["result"] is None


def test_replaced_slot_after_permanent_cancel_is_not_released(tmp_path):
    with archived(tmp_path) as case:
        pending = prepare(case)
        cancel_permanent(case, pending)
        assert case.store.release_work_slot(case.permit.work_uid)
        assert case.store.acquire_work_slot("DELIVERY", str(uuid.uuid4()), 1, {"phase": "NATIVE_RUNNING"})
        slot = case.store.get_work_slot()
        with pytest.raises(ValueError, match="original work slot has changed"):
            apply(case)
        assert case.store.get_work_slot() == slot
        assert case.store.get_command(case.permit.command_uid)["result"]["nativeIssueCompletion"]["state"] == "PREPARED"


@pytest.mark.parametrize("failed", [False, True])
def test_reliably_saved_complete_packet_prevents_issue_cancellation_even_after_reboot(tmp_path, failed):
    with original_work(tmp_path) as case:
        raw = case.raw
        if failed:
            value = uart.decode_payload("WORK_RESULT", raw)
            value.update(finishReason="FAILED", finalKind="UNAVAILABLE", finalWeightGrams=0,
                finalElapsedMs=5000, finalSampleCount=0, finalSpanGrams=0, finalFaultCode="WEIGHT_TIMEOUT")
            value["resultDigestSha256"] = uart.compute_result_digest(value)
            raw = uart.encode_payload("WORK_RESULT", value)
        case.store.save_native_mcu_result(raw)
        boot = bind_boot(case.store)
        decision = case.store.archive_native_delivery_issue(case.permit, case.start["mcuCommandUid"],
            device_name="device-1", current_boot=lambda: boot)
        assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
        assert prepare(case) == apply(case) == {"state": "ISSUE_NOT_ARCHIVED"}
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.store.get_command(case.permit.command_uid)["result"] is None
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def test_an_orphan_slot_completion_marker_is_not_silently_repaired(tmp_path):
    with archived(tmp_path) as case:
        context = case.store.get_work_slot()["context"] | {"nativeIssueCompletion": None}
        case.store.update_work_context(case.permit.work_uid, context)
        with pytest.raises(ValueError, match="slot receipt"):
            prepare(case)
        assert case.store.get_work_slot()["context"] == context
        assert case.store.get_command(case.permit.command_uid)["result"] is None


@pytest.mark.parametrize("field", ["permitUid", "commandUid", "workUid", "workType", "requestDigestSha256"])
def test_a_snapshot_of_another_permanent_permit_cannot_prepare_original_issue(tmp_path, field):
    with archived(tmp_path) as case:
        snapshot = case.safety.get_job_permit(case.permit.permit_uid)
        snapshot[field] = "CLEAN" if field == "workType" else "0" * 64 if field == "requestDigestSha256" else str(uuid.uuid4())
        with pytest.raises(ValueError, match="original permanent permit"):
            case.store.prepare_native_issue_completion(case.permit, case.start["mcuCommandUid"],
                device_name="device-1", permit_snapshot=snapshot)
        assert case.store.get_command(case.permit.command_uid)["result"] is None
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid


def test_archive_without_report_cannot_prepare_or_apply(tmp_path):
    with archived(tmp_path, confirmation=False, report=False) as case:
        assert prepare(case) == apply(case) == {"state": "WAITING_FOR_ISSUE_REPORT"}
        assert case.store.get_command(case.permit.command_uid)["result"] is None
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid


def test_apply_requires_a_previously_frozen_local_receipt(tmp_path):
    with archived(tmp_path) as case:
        with pytest.raises(ValueError, match="has not frozen"):
            apply(case)
        assert case.store.get_command(case.permit.command_uid)["result"] is None
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid


def test_malformed_original_result_is_preserved_and_rejected(tmp_path):
    with archived(tmp_path) as case:
        with case.store.transaction() as conn:
            conn.execute("UPDATE command_inbox SET result_json='[]' WHERE command_uid=?", (case.permit.command_uid,))
        with pytest.raises(ValueError, match="command result is malformed"):
            prepare(case)
        assert case.store.get_command(case.permit.command_uid)["result"] == []
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED", "REJECTED"])
def test_another_terminal_command_result_cannot_be_overwritten(tmp_path, state):
    with archived(tmp_path) as case:
        with case.store.transaction() as conn:
            conn.execute("UPDATE command_inbox SET state=?,result_json=? WHERE command_uid=?",
                (state, json.dumps({"anotherOutcome": "retained"}), case.permit.command_uid))
        before = case.store.get_command(case.permit.command_uid)
        with pytest.raises(ValueError, match="another terminal command result"):
            prepare(case)
        assert case.store.get_command(case.permit.command_uid) == before
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid


def test_corrupt_prepared_digest_is_not_replaced_from_current_archive(tmp_path):
    with archived(tmp_path) as case:
        prepare(case)
        result = case.store.get_command(case.permit.command_uid)["result"]
        result["nativeIssueCompletion"]["evidenceSha256"] = "0" * 64
        with case.store.transaction() as conn:
            conn.execute("UPDATE command_inbox SET result_json=? WHERE command_uid=?",
                (json.dumps(result), case.permit.command_uid))
        with pytest.raises(ValueError, match="receipt is corrupt"):
            prepare(case)
        assert case.store.get_command(case.permit.command_uid)["result"] == result
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
