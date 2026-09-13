"""One exact delivery terminal weight timeout closes FAILED, never as success.

Uses real SQLite, original report confirmations and the permanent updater store;
wire packets are explicit fixtures, not live hardware or cloud operations.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import sqlite3
import uuid
import pytest

from native_business_completion import MARKER, NativeBusinessCompleter
from hardware.tests.test_native_simplified_result import original_work
from hardware.tests.test_native_result_confirmation import confirmation_wire
from hardware.tests.test_native_business_completion import reported, put_baseline
from hardware.tests.test_mcu_simplified_execution import library, runtime
from hardware.tests.native_confirmation_fixture import autonomous_result_case
from native_result_report import NativeResultReporter
from onenet_wire import canonical_payload_sha256
import uart2_protocol as uart


def timeout_packet(raw, *, sample_count=0):
    value = uart.decode_payload("WORK_RESULT", raw)
    value.update(finishReason="FAILED", finalKind="UNAVAILABLE", finalWeightGrams=0,
        finalElapsedMs=5000, finalSampleCount=sample_count, finalSpanGrams=0, finalFaultCode="WEIGHT_TIMEOUT")
    value["resultDigestSha256"] = uart.compute_result_digest(value)
    return uart.encode_payload("WORK_RESULT", value)


@contextmanager
def failed_report(tmp_path, *, confirmation=True, quarantined=False, sample_count=0):
    with original_work(tmp_path) as case:
        case.clean = False
        case.raw = timeout_packet(case.raw, sample_count=sample_count)
        case.store.save_native_mcu_result(case.raw)
        case.report = case.reporter.prepare(case.permit, case.start["mcuCommandUid"])
        assert case.report["state"] == "REPORT_CREATED"
        if confirmation:
            _, _, command = confirmation_wire(case, case.report["eventUid"], quarantined=quarantined)
            assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        case.completer = NativeBusinessCompleter(case.store, case.safety, device_name="device-1")
        yield case


def prepare(case):
    return case.store.prepare_native_business_completion(case.permit, case.start["mcuCommandUid"], device_name="device-1",
        permit_snapshot=case.safety.get_job_permit(case.permit.permit_uid))


def apply(case):
    return case.store.apply_native_business_completion(case.permit, case.start["mcuCommandUid"], device_name="device-1",
        permit_snapshot=case.safety.get_job_permit(case.permit.permit_uid))


@pytest.mark.parametrize("sample_count", [0, 4])
def test_backend_applied_terminal_timeout_completes_failed_without_bag_or_admission_changes(tmp_path, sample_count):
    with failed_report(tmp_path, sample_count=sample_count) as case:
        case.store.set_state("native_blocking_fault", "WEIGHT_TIMEOUT")
        events = case.store.list_pending_events()
        pending = prepare(case)
        assert pending["state"] == "PREPARED"
        assert pending["completionOutcome"] == "FAILED"
        assert pending["completionUid"] == case.permit.command_uid
        command = case.store.get_command(case.permit.command_uid)
        assert command["state"] == "FAILED" and command["last_error"] == "WEIGHT_TIMEOUT"
        assert command["result"][MARKER]["evidence"]["completionOutcome"] == "FAILED"
        assert command["result"][MARKER]["evidence"]["failureCode"] == "WEIGHT_TIMEOUT"
        assert case.store.get_work_slot()["work_state"] == "COMPLETING"
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        done = case.completer.complete(case.permit, case.start["mcuCommandUid"])
        assert done == pending | {"state": "COMPLETED"}
        assert not done["baselineApplied"]
        permanent = case.safety.get_job_permit(case.permit.permit_uid)
        assert permanent["state"] == "COMPLETED" and permanent["completionOutcome"] == "FAILED"
        assert permanent["completionDigestSha256"] == pending["evidenceSha256"]
        assert case.store.get_work_slot() is None
        assert case.store.get_command(case.permit.command_uid)["state"] == "FAILED"
        assert case.store.get_state("native_blocking_fault") == "WEIGHT_TIMEOUT"
        assert case.store._conn.execute("SELECT COUNT(*) FROM bag_baseline").fetchone()[0] == 0
        assert case.store.list_pending_events() == events


def test_changed_original_slot_receipt_cannot_release_weight_failure(tmp_path):
    with failed_report(tmp_path) as case:
        pending = prepare(case)
        case.safety.complete_job(case.permit, completion_uid=pending["completionUid"],
            outcome="FAILED", completion_digest_sha256=pending["evidenceSha256"])
        context = case.store.get_work_slot()["context"]
        context[MARKER]["evidenceSha256"] = "0" * 64
        case.store.update_work_context(case.permit.work_uid, context)
        with pytest.raises(ValueError, match="slot receipt"):
            apply(case)
        assert case.store.get_work_slot()["context"] == context
        assert case.store.get_command(case.permit.command_uid)["result"][MARKER]["state"] == "PREPARED"


@pytest.mark.parametrize("confirmation", ["none", "platform", "quarantined"])
def test_weight_failure_does_not_release_before_its_exact_backend_business_confirmation(tmp_path, confirmation):
    with failed_report(tmp_path, confirmation=confirmation == "quarantined", quarantined=True) as case:
        if confirmation == "platform":
            event = case.store.get_event(case.report["eventUid"])
            case.store.record_event_platform_reply(event["edge_event_sequence"], 200)
        slot = case.store.get_work_slot()
        expected = "BACKEND_RESULT_NOT_APPLIED" if confirmation == "quarantined" else "WAITING_FOR_BACKEND_CONFIRMATION"
        assert prepare(case) == apply(case) == {"state": expected}
        assert case.store.get_work_slot() == slot
        assert case.store.get_command(case.permit.command_uid)["result"] is None
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


@pytest.mark.parametrize("permanent_already_applied", [False, True])
def test_restart_and_lost_permanent_reply_reuse_the_exact_failed_receipt(tmp_path, permanent_already_applied):
    with failed_report(tmp_path) as case:
        pending = prepare(case)
        if permanent_already_applied:
            case.safety.complete_job(case.permit, completion_uid=pending["completionUid"], outcome="FAILED",
                completion_digest_sha256=pending["evidenceSha256"])
        case.store.close()
        case.updater.close()
        case.store.initialize()
        case.updater.initialize()
        assert prepare(case) == pending
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.completer.complete(case.permit, case.start["mcuCommandUid"]) == pending | {"state": "COMPLETED"}
        assert case.safety.get_job_permit(case.permit.permit_uid)["completionOutcome"] == "FAILED"
        assert case.store.get_work_slot() is None


@pytest.mark.parametrize("changed", ["SUCCEEDED", "CANCELLED", "uid", "digest"])
def test_wrong_real_permanent_completion_cannot_release_failed_delivery(tmp_path, changed):
    with failed_report(tmp_path) as case:
        pending = prepare(case)
        case.safety.complete_job(case.permit,
            completion_uid=str(uuid.uuid4()) if changed == "uid" else pending["completionUid"],
            outcome=changed if changed in {"SUCCEEDED", "CANCELLED"} else "FAILED",
            completion_digest_sha256="0" * 64 if changed == "digest" else pending["evidenceSha256"])
        slot = case.store.get_work_slot()
        for action in (prepare, apply):
            with pytest.raises(ValueError, match="exact completed permanent receipt"):
                action(case)
        assert case.store.get_work_slot() == slot
        assert case.store.get_command(case.permit.command_uid)["state"] == "FAILED"


def test_active_permit_cannot_apply_and_first_prepare_requires_active_not_completed(tmp_path):
    with failed_report(tmp_path) as case:
        pending = prepare(case)
        with pytest.raises(ValueError, match="exact completed permanent receipt"):
            apply(case)
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
    other = tmp_path / "already-completed"
    other.mkdir()
    with failed_report(other) as case:
        case.safety.complete_job(case.permit, completion_uid=case.permit.command_uid, outcome="FAILED",
            completion_digest_sha256=pending["evidenceSha256"])
        with pytest.raises(ValueError, match="original active permanent permit"):
            prepare(case)
        assert case.store.get_command(case.permit.command_uid)["result"] is None


def test_completed_old_failure_cannot_touch_new_business_bag_or_restore_success(tmp_path):
    with failed_report(tmp_path) as case:
        done = case.completer.complete(case.permit, case.start["mcuCommandUid"])
        bag, work = str(uuid.uuid4()), str(uuid.uuid4())
        assert case.store.acquire_work_slot("CLEAN", work, 1, {"phase": "NATIVE_RUNNING", "bagUid": bag})
        baseline = put_baseline(case.store, bag, source_work_uid=work, grams=456)
        case.store.set_state("native_blocking_fault", "OTHER_FAULT")
        slot, events = case.store.get_work_slot(), case.store.list_pending_events()
        case.store.close()
        case.store.initialize()
        assert prepare(case) == apply(case) == done
        assert case.store.get_work_slot() == slot
        assert case.store.get_bag_baseline(bag) == baseline
        assert case.store.get_state("native_blocking_fault") == "OTHER_FAULT"
        assert case.store.get_command(case.permit.command_uid)["state"] == "FAILED"
        assert case.store.list_pending_events() == events


def test_new_slot_between_permanent_failure_and_local_apply_is_not_released(tmp_path):
    with failed_report(tmp_path) as case:
        pending = prepare(case)
        case.safety.complete_job(case.permit, completion_uid=pending["completionUid"], outcome="FAILED",
            completion_digest_sha256=pending["evidenceSha256"])
        assert case.store.release_work_slot(case.permit.work_uid)
        assert case.store.acquire_work_slot("DELIVERY", str(uuid.uuid4()), 1, {"phase": "NATIVE_RUNNING"})
        slot = case.store.get_work_slot()
        with pytest.raises(ValueError, match="work slot has changed"):
            apply(case)
        assert case.store.get_work_slot() == slot
        assert case.store.get_command(case.permit.command_uid)["result"][MARKER]["state"] == "PREPARED"


@pytest.mark.parametrize("stage", ["prepare", "apply"])
def test_failed_local_transaction_preserves_slot_command_and_receipt_together(tmp_path, stage):
    with failed_report(tmp_path) as case:
        if stage == "apply":
            pending = prepare(case)
            case.safety.complete_job(case.permit, completion_uid=pending["completionUid"], outcome="FAILED",
                completion_digest_sha256=pending["evidenceSha256"])
        command, slot = case.store.get_command(case.permit.command_uid), case.store.get_work_slot()
        with case.store.transaction() as conn:
            conn.execute("""CREATE TRIGGER fail_weight_completion BEFORE UPDATE ON work_slot
                BEGIN SELECT RAISE(ABORT, 'injected weight completion write failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            (prepare if stage == "prepare" else apply)(case)
        assert case.store.get_command(case.permit.command_uid) == command
        assert case.store.get_work_slot() == slot
        with case.store.transaction() as conn:
            conn.execute("DROP TRIGGER fail_weight_completion")
        assert case.completer.complete(case.permit, case.start["mcuCommandUid"])["completionOutcome"] == "FAILED"


@pytest.mark.parametrize("changed", ["permit", "start", "device"])
def test_another_original_identity_cannot_close_weight_failure(tmp_path, changed):
    with failed_report(tmp_path) as case:
        permit = replace(case.permit, command_uid=str(uuid.uuid4())) if changed == "permit" else case.permit
        start = str(uuid.uuid4()) if changed == "start" else case.start["mcuCommandUid"]
        device = "different-device" if changed == "device" else "device-1"
        slot = case.store.get_work_slot()
        with pytest.raises(ValueError):
            case.store.prepare_native_business_completion(permit, start, device_name=device,
                permit_snapshot=case.safety.get_job_permit(case.permit.permit_uid))
        assert case.store.get_work_slot() == slot
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


@pytest.mark.parametrize("clean", [False, True])
def test_normal_completion_evidence_and_digest_remain_exactly_compatible(tmp_path, clean):
    with reported(tmp_path, clean=clean) as case:
        event = json.loads(case.store.get_event(case.report["eventUid"])["payload_json"])
        packet = uart.decode_payload("WORK_RESULT", case.raw)
        payload, baseline = event["payload"], None
        if clean:
            measurement = payload["cleanerConfirmedFinalMeasurement"]
            baseline = dict(bag_uid=payload["newBagUid"], weight_grams=packet["finalWeightGrams"],
                source_kind="NATIVE_CLEAN_POST", source_work_type="CLEAN_OPERATION", source_work_uid=case.permit.work_uid,
                source_mcu_boot_id=measurement["mcuBootId"], source_mcu_event_sequence=measurement["mcuEventSequence"],
                source_observed_at=None, measurement_uid=measurement["measurementUid"])
        # Frozen v1 success projection: deliberately no outcome/failure fields.
        expected = dict(profile="ecobin-native-business-completion-v1", deviceName="device-1", permit=asdict(case.permit),
            startCommandUid=case.start["mcuCommandUid"], eventUid=case.report["eventUid"], eventPayloadSha256=event["payloadSha256"],
            mcuBootId=packet["mcuBootId"], resultSequence=packet["resultSequence"], resultDigestSha256=packet["resultDigestSha256"],
            portNo=packet["portNo"], confirmation=case.store.get_native_result_confirmation(case.permit,
                case.start["mcuCommandUid"], device_name="device-1"), baseline=baseline)
        pending = prepare(case)
        marker = case.store.get_command(case.permit.command_uid)["result"][MARKER]
        assert marker["evidence"] == expected
        assert marker["evidenceSha256"] == canonical_payload_sha256(expected)
        assert pending["completionOutcome"] == "SUCCEEDED"
        case.safety.complete_job(case.permit, completion_uid=pending["completionUid"], outcome="SUCCEEDED",
            completion_digest_sha256=canonical_payload_sha256(expected))
        case.store.close()
        case.store.initialize()
        assert apply(case)["completionOutcome"] == "SUCCEEDED"


@pytest.mark.parametrize("kind", ["first_weight_failure", "control_cancelled", "clean_failure", "short_timeout", "enough_samples"])
def test_other_failure_kinds_do_not_enter_the_terminal_delivery_weight_exit(tmp_path, kind):
    with original_work(tmp_path, clean=kind == "clean_failure") as case:
        value = uart.decode_payload("WORK_RESULT", timeout_packet(case.raw))
        if kind == "first_weight_failure":
            value.update(deliveryRoundCount=0, initialKind="UNAVAILABLE", initialWeightGrams=0,
                initialElapsedMs=5000, initialSampleCount=0, initialSpanGrams=0, initialFaultCode="WEIGHT_TIMEOUT")
            for field in value:
                if field.startswith("final"):
                    value[field] = ("NOT_TAKEN" if field == "finalKind" else "NONE" if field == "finalFaultCode"
                                    else str(uuid.UUID(int=0)) if field == "finalMeasurementUid" else 0)
        elif kind == "control_cancelled":
            value["finishReason"] = "CANCELLED"
        elif kind == "short_timeout":
            value["finalElapsedMs"] = 4999
        elif kind == "enough_samples":
            value["finalSampleCount"] = 5
        value["resultDigestSha256"] = uart.compute_result_digest(value)
        case.store.save_native_mcu_result(uart.encode_payload("WORK_RESULT", value))
        assert case.reporter.prepare(case.permit, case.start["mcuCommandUid"])["state"] == "WAITING_FOR_RESULT_POLICY"
        assert prepare(case) == {"state": "WAITING_FOR_REPORT"}
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.store.get_command(case.permit.command_uid)["result"] is None
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def test_archived_issue_with_late_timeout_result_stays_on_its_issue_handler(tmp_path):
    from hardware.tests.test_native_issue_completion import archived
    with archived(tmp_path) as case:
        case.store.save_native_mcu_result(timeout_packet(case.raw))
        assert prepare(case) == apply(case) == {"state": "ISSUE_ARCHIVED"}
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.store.get_command(case.permit.command_uid)["result"] is None
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


@pytest.mark.parametrize("changed", ["null_marker", "failure_outcome", "failure_code", "command_state", "command_reason", "orphan_slot"])
def test_conflicting_failure_receipt_is_not_replaced_or_promoted_to_success(tmp_path, changed):
    with failed_report(tmp_path) as case:
        if changed != "orphan_slot":
            prepare(case)
        command = case.store.get_command(case.permit.command_uid)
        if changed == "orphan_slot":
            context = case.store.get_work_slot()["context"] | {MARKER: {"evidenceSha256": "0" * 64}}
            case.store.update_work_context(case.permit.work_uid, context)
        else:
            result = command["result"]
            if changed == "null_marker":
                result[MARKER] = None
            elif changed == "failure_outcome":
                result[MARKER]["evidence"]["completionOutcome"] = "SUCCEEDED"
            elif changed == "failure_code":
                result[MARKER]["evidence"]["failureCode"] = "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
            with case.store.transaction() as conn:
                conn.execute("UPDATE command_inbox SET result_json=?, state=?, last_error=? WHERE command_uid=?",
                    (json.dumps(result), "COMPLETED" if changed == "command_state" else command["state"],
                     "SOMETHING_ELSE" if changed == "command_reason" else command["last_error"], case.permit.command_uid))
        slot, command = case.store.get_work_slot(), case.store.get_command(case.permit.command_uid)
        with pytest.raises(ValueError):
            prepare(case)
        assert case.store.get_work_slot() == slot
        assert case.store.get_command(case.permit.command_uid) == command
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"


def test_actual_autonomous_c_five_second_final_timeout_finishes_failed_without_new_writes(runtime, tmp_path):
    with autonomous_result_case(runtime, tmp_path, clean=False, samples=()) as case:
        report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(case.permit, case.start["mcuCommandUid"])
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        assert event["payload"]["completionReason"] == "TERMINAL_WEIGHT_FAILURE"
        assert event["payload"]["finalPostCloseMeasurement"]["reportedWeightGrams"] is None
        _, _, command = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=command, device_name="device-1") == "ACCEPTED"
        before = list(case.wire.sent)
        done = NativeBusinessCompleter(case.store, case.safety, device_name="device-1").complete(case.permit, case.start["mcuCommandUid"])
        assert done["completionOutcome"] == "FAILED" and done["state"] == "COMPLETED"
        assert case.store.get_work_slot() is None
        assert case.store.get_command(case.permit.command_uid)["state"] == "FAILED"
        assert case.safety.get_job_permit(case.permit.permit_uid)["completionOutcome"] == "FAILED"
        assert case.wire.sent == before
        assert not case.store.list_native_work_actuator_events(case.permit.work_uid)
