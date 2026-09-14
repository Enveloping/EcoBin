"""Value-free completion for explicit MCU FAILED/CANCELLED results.

These tests use real SQLite and the permanent safety ledger.  Wire results are
fixtures; the MCU execution suite separately verifies their C producer.
"""
from contextlib import contextmanager
import json
import sqlite3
import uuid

import pytest

from native_control_failure import MARKER, RESULT_PROFILE
from hardware.tests.test_mcu_simplified_execution import library, runtime, tick
from hardware.tests.test_native_business_runtime import poll_until
from hardware.tests.test_native_runtime_control_failure import dropped_start_decision
from hardware.tests.test_native_simplified_result import original_work
from hardware.tests.test_native_weight_failure_completion import timeout_packet
import uart2_protocol as uart


def _not_taken(value, role):
    value.update({
        role + "Kind": "NOT_TAKEN",
        role + "MeasurementUid": str(uuid.UUID(int=0)),
        role + "SourceMcuBootId": 0,
        role + "McuEventSequence": 0,
        role + "WeightGrams": 0,
        role + "ElapsedMs": 0,
        role + "SampleCount": 0,
        role + "SpanGrams": 0,
        role + "CalibrationVersion": 0,
        role + "FaultCode": "NONE",
    })


def failure_packet(raw, kind):
    value = uart.decode_payload("WORK_RESULT", raw)
    if kind == "initial":
        value.update(
            finishReason="FAILED",
            physicalCloseConfirmed=False,
            deliveryRoundCount=0,
            cleanActionSequence=0,
            initialKind="UNAVAILABLE",
            initialWeightGrams=0,
            initialElapsedMs=5000,
            initialSampleCount=0,
            initialSpanGrams=0,
            initialFaultCode="WEIGHT_TIMEOUT",
        )
        _not_taken(value, "final")
    elif kind == "clean_final":
        value.update(
            finishReason="FAILED",
            physicalCloseConfirmed=True,
            finalKind="UNAVAILABLE",
            finalWeightGrams=0,
            finalElapsedMs=5000,
            finalSampleCount=0,
            finalSpanGrams=0,
            finalFaultCode="WEIGHT_TIMEOUT",
        )
    elif kind == "cancelled":
        value.update(finishReason="CANCELLED", physicalCloseConfirmed=False)
        _not_taken(value, "final")
    elif kind == "failed":
        value.update(finishReason="FAILED", physicalCloseConfirmed=False)
    else:
        raise AssertionError(kind)
    value["resultDigestSha256"] = uart.compute_result_digest(value)
    return uart.encode_payload("WORK_RESULT", value)


@contextmanager
def failed_result(tmp_path, *, clean, kind):
    with original_work(tmp_path, clean=clean) as case:
        case.raw = failure_packet(case.raw, kind)
        case.store.save_native_mcu_result(case.raw)
        yield case


def prepare(case):
    return case.store.prepare_native_result_failure(
        case.permit,
        case.start["mcuCommandUid"],
        device_name="device-1",
    )


def apply(case):
    return case.store.apply_native_control_failure(
        case.permit,
        case.start["mcuCommandUid"],
        device_name="device-1",
        permit_snapshot=case.safety.get_job_permit(case.permit.permit_uid),
    )


@pytest.mark.parametrize(
    ("clean", "kind", "reason", "interlocked"),
    [
        (False, "initial", "MCU_INITIAL_WEIGHT_UNAVAILABLE", False),
        (True, "initial", "MCU_INITIAL_WEIGHT_UNAVAILABLE", False),
        (True, "clean_final", "MCU_CLEAN_FINAL_WEIGHT_UNAVAILABLE", True),
        (False, "failed", "MCU_WORK_FAILED", False),
        (False, "cancelled", "MCU_WORK_CANCELLED", False),
        # A clean START automatically begins its first unlock after a good
        # initial weight; actionSequence=0 therefore does not prove no action.
        (True, "cancelled", "MCU_WORK_CANCELLED", True),
    ],
)
def test_complete_failure_is_problem_fact_only_then_releases_exact_work(
    tmp_path, clean, kind, reason, interlocked
):
    with failed_result(tmp_path, clean=clean, kind=kind) as case:
        saved = uart.decode_payload("WORK_RESULT", case.raw)
        pending = prepare(case)
        assert pending["state"] == "PREPARED"
        command = case.store.get_command(case.permit.command_uid)
        marker = command["result"][MARKER]
        assert command["state"] == "FAILED" and command["last_error"] == reason
        assert marker["evidence"]["profile"] == RESULT_PROFILE
        assert marker["evidence"]["resultDigestSha256"] == saved["resultDigestSha256"]
        assert marker["evidence"]["businessValue"] == "NONE"
        assert marker["evidence"]["cleanBagInterlockRequired"] is interlocked
        assert case.store.get_native_mcu_result(
            saved["mcuBootId"], saved["resultSequence"]
        )["payload"] == case.raw
        assert case.store.get_work_slot()["work_state"] == "COMPLETING"
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        events = [json.loads(row["payload_json"]) for row in case.store.list_pending_events()]
        assert len(events) == 1
        assert events[0]["eventType"] == "DEVICE_COMMAND_OBSERVED"
        assert events[0]["payload"] == {
            "observedCommandType": "START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION",
            "stage": "FAILED",
            "mcuCommandUid": case.start["mcuCommandUid"],
            "errorCode": reason,
        }
        assert all(event["eventType"] not in {"DELIVERY_COMPLETE", "CLEAN_COMPLETE"} for event in events)
        assert case.store.clean_restart_interlock_active(1) is interlocked
        assert case.store._conn.execute("SELECT COUNT(*) FROM bag_baseline").fetchone()[0] == 0

        case.safety.complete_job(
            case.permit,
            completion_uid=pending["completionUid"],
            outcome="FAILED",
            completion_digest_sha256=pending["evidenceSha256"],
        )
        done = apply(case)
        assert done == pending | {"state": "COMPLETED"}
        assert case.store.get_work_slot() is None
        permanent = case.safety.get_job_permit(case.permit.permit_uid)
        assert permanent["state"] == "COMPLETED"
        assert permanent["completionOutcome"] == "FAILED"
        assert permanent["completionDigestSha256"] == pending["evidenceSha256"]
        assert case.store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
        assert case.store._conn.execute("SELECT COUNT(*) FROM bag_baseline").fetchone()[0] == 0


def test_result_failure_prepare_is_atomic_and_retryable_after_sqlite_write_failure(tmp_path):
    with failed_result(tmp_path, clean=True, kind="clean_final") as case:
        original_command = case.store.get_command(case.permit.command_uid)
        original_slot = case.store.get_work_slot()
        with case.store.transaction() as conn:
            conn.execute("""CREATE TRIGGER fail_result_completion BEFORE UPDATE ON work_slot
                BEGIN SELECT RAISE(ABORT, 'injected result completion write failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            prepare(case)
        assert case.store.get_command(case.permit.command_uid) == original_command
        assert case.store.get_work_slot() == original_slot
        assert case.store.list_pending_events() == []
        assert not case.store.clean_restart_interlock_active(1)
        assert case.store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
        with case.store.transaction() as conn:
            conn.execute("DROP TRIGGER fail_result_completion")
        assert prepare(case)["state"] == "PREPARED"


def test_restart_and_lost_permanent_reply_reuse_exact_result_failure(tmp_path):
    with failed_result(tmp_path, clean=False, kind="cancelled") as case:
        pending = prepare(case)
        case.safety.complete_job(case.permit, completion_uid=pending["completionUid"],
            outcome="FAILED", completion_digest_sha256=pending["evidenceSha256"])
        case.store.close()
        case.updater.close()
        case.store.initialize()
        case.updater.initialize()
        assert prepare(case) == pending
        assert apply(case) == pending | {"state": "COMPLETED"}
        case.store.close()
        case.store.initialize()
        assert apply(case) == pending | {"state": "COMPLETED"}
        assert case.store.get_work_slot() is None
        assert len(case.store.list_pending_events()) == 1


def test_permanent_failure_cannot_release_a_replacement_work_slot(tmp_path):
    with failed_result(tmp_path, clean=False, kind="cancelled") as case:
        pending = prepare(case)
        case.safety.complete_job(case.permit, completion_uid=pending["completionUid"],
            outcome="FAILED", completion_digest_sha256=pending["evidenceSha256"])
        assert case.store.release_work_slot(case.permit.work_uid)
        replacement_uid = str(uuid.uuid4())
        assert case.store.acquire_work_slot("DELIVERY", replacement_uid, 1, {"phase": "NATIVE_RUNNING"})
        replacement = case.store.get_work_slot()
        with pytest.raises(ValueError, match="work slot has changed"):
            apply(case)
        assert case.store.get_work_slot() == replacement


def test_existing_delivery_postclose_timeout_keeps_its_abnormal_order_path(tmp_path):
    with original_work(tmp_path) as case:
        case.raw = timeout_packet(case.raw)
        case.store.save_native_mcu_result(case.raw)
        assert prepare(case) == {"state": "OTHER_TERMINAL_PATH"}
        assert case.reporter.prepare(case.permit, case.start["mcuCommandUid"])["state"] == "REPORT_CREATED"
        assert case.store.get_command(case.permit.command_uid)["result"] is None
        assert not case.store.clean_restart_interlock_active(1)


@pytest.mark.parametrize("mode", ["delivery_initial", "clean_initial"])
def test_foreground_runtime_consumes_actual_mcu_failure_without_old_actions(runtime, tmp_path, mode):
    clean = mode.startswith("clean")
    with dropped_start_decision(runtime, tmp_path, clean=clean) as (case, owner):
        before = len(case.wire.sent)
        now = case.wire.now
        now = tick(runtime, now, 5000)
        now = tick(runtime, now, 0)
        case.wire.now = now
        poll_until(owner, case.clock, lambda: case.store.get_work_slot() is None)

        command = case.store.get_command(case.business["commandUid"])
        marker = command["result"][MARKER]
        assert command["state"] == "FAILED"
        assert marker["state"] == "APPLIED"
        assert marker["evidence"]["profile"] == RESULT_PROFILE
        assert case.safety.get_job_permit(case.permit.permit_uid)["completionOutcome"] == "FAILED"
        assert not case.store.clean_restart_interlock_active(1)
        events = [json.loads(row["payload_json"]) for row in case.store.list_pending_events()
            if json.loads(row["payload_json"])["commandUid"] == case.business["commandUid"]]
        assert [event["eventType"] for event in events] == ["DEVICE_COMMAND_OBSERVED"]
        sent = [frame["messageName"] for frame in case.wire.sent[before:]]
        assert not {"AUTHORIZE_DELIVERY_FIRST_OPEN", "UNLOCK_CLEAN_DOOR", "SAFE_CLOSE"} & set(sent)
