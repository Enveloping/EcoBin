"""Native START-response timeout closes one work without replay or settlement."""
from contextlib import contextmanager
import json
import time

import pytest

from job_safety import JobPermit, JobSafetyError, command_request_digest
from hardware.tests.test_mcu_work_preparation import take_samples
from hardware.tests.test_mcu_simplified_execution import library, runtime, select, tick
from hardware.tests.test_native_business_runtime import (
    apply_configuration, await_start_facts, completed_first_work, open_owner, poll_until, start_command,
)
from hardware.tests.test_native_runtime_configuration_reload import held_result_frame
from hardware.tests.test_simplified_mcu_pi_business import finish_delivery_round
import uart2_protocol as uart


def matching_observations(case, command_uid):
    return [json.loads(row["payload_json"]) for row in case.store.list_pending_events()
        if row["event_type"] == "DEVICE_COMMAND_OBSERVED"
        and json.loads(row["payload_json"])["commandUid"] == command_uid]


@contextmanager
def dropped_start_decision(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        business = start_command()
        assert case.store.receive_command(business["commandUid"], business["commandType"], business) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == business["commandUid"]
        pending = owner.start_delivery_command(business)
        uid = pending["mcu_command_uid"]
        case.serial.drop = lambda decoded: (decoded["messageName"] in {"COMMAND_DECISION", "COMMAND_QUERY_RESULT"}
            and uart.decode_payload(decoded["messageName"], decoded["payload"])["mcuCommandUid"] == uid)
        poll_until(owner, case.clock, lambda: case.store.get_native_command(uid)["write_claimed"])
        case.business, case.uid = business, uid
        case.start = uart.decode_payload("START_DELIVERY_SESSION", case.store.get_native_command(uid)["payload"])
        case.permit = JobPermit(business["commandUid"], business["payload"]["sessionUid"],
            business["commandUid"], "DELIVERY", command_request_digest(business))
        yield case, owner


def wait_for_control_failure(case, owner):
    # Valid DEVICE_FACTS replies keep the global link alive.  Only the exact
    # original START decision/query is dropped, so this exercises the separate
    # per-command deadline rather than the whole-link timeout.
    for _ in range(150):
        case.clock.now += 100
        owner.poll()
        if case.store.get_work_slot() is None:
            return
        time.sleep(0.001)
    raise AssertionError("control failure did not release its exact local work")


def test_unanswered_start_times_out_despite_healthy_facts_and_blocks_new_business(runtime, tmp_path):
    with dropped_start_decision(runtime, tmp_path) as (case, owner):
        wait_for_control_failure(case, owner)
        record = case.store.get_native_command(case.uid)
        assert record["write_claimed"] and record["decision_outcome"] is None
        command = case.store.get_command(case.business["commandUid"])
        assert command["state"] == "FAILED" and command["last_error"] == "MCU_COMMUNICATION_UNAVAILABLE"
        marker = command["result"]["nativeControlFailure"]
        assert marker["state"] == "APPLIED"
        assert marker["evidence"]["writeClaimed"] is True
        assert marker["evidence"]["stage"] == "FAILED"
        permanent = case.safety.get_job_permit(case.permit.permit_uid)
        assert permanent["state"] == "COMPLETED"
        assert permanent["completionOutcome"] == "FAILED"
        assert permanent["completionDigestSha256"] == marker["evidenceSha256"]
        observed = matching_observations(case, case.business["commandUid"])
        assert len(observed) == 1
        assert observed[0]["payload"] == dict(observedCommandType="START_DELIVERY_SESSION", stage="FAILED",
            mcuCommandUid=case.uid, errorCode="MCU_COMMUNICATION_UNAVAILABLE")
        fault = case.store.get_active_edge_fault("UART", "UART_PROTOCOL")
        assert fault is not None and fault["severity"] == "BLOCK_DEVICE"
        assert json.loads(fault["detail_json"]) == dict(profile="native-control-communication-v1",
            reasonCode="MCU_COMMUNICATION_UNAVAILABLE", automaticRecovery=False)
        assert case.store.get_state("native_blocking_fault") == "MCU_COMMUNICATION_UNAVAILABLE"
        assert len(case.store.list_native_result_report_tasks()) == 1  # Only the fixture's completed work.
        with pytest.raises(JobSafetyError) as blocked:
            owner.start_delivery_command(start_command())
        assert blocked.value.code == "MCU_COMMUNICATION_UNAVAILABLE"


def test_operator_clears_exact_communication_fault_only_after_fresh_reply(
    runtime,
    tmp_path,
):
    with dropped_start_decision(runtime, tmp_path) as (case, owner):
        wait_for_control_failure(case, owner)
        status = owner.communication_fault_status()
        assert status == {
            "reasonCode": "MCU_COMMUNICATION_UNAVAILABLE",
            "faultUid": status["faultUid"],
            "mcuBootId": case.start["targetMcuBootId"],
            "freshCommunicationConfirmed": True,
            "activeWorkUid": None,
            "manualRecoveryEligible": True,
        }
        assert status["faultUid"]

        result = owner.confirm_communication_fault_recovered({
            "expectedFaultUid": status["faultUid"],
            "reason": "现场修复串口连接并确认设备事实查询已恢复",
            "causeFixedConfirmed": True,
        })

        assert result == {
            "disposition": "RECOVERED",
            "faultUid": status["faultUid"],
            "mcuBootId": case.start["targetMcuBootId"],
            "newBusinessAdmissionRecheckRequired": True,
        }
        assert case.store.get_state("native_blocking_fault") == ""
        assert case.store.get_active_edge_fault(
            "UART", "UART_PROTOCOL"
        ) is None
        fault = case.store._conn.execute(
            "SELECT * FROM edge_fault_state WHERE fault_uid=?",
            (status["faultUid"],),
        ).fetchone()
        assert fault["lifecycle"] == "RECOVERED"
        evidence = json.loads(fault["recovery_evidence"])
        assert evidence["profile"] == (
            "native-control-communication-manual-recovery-v1"
        )
        assert evidence["causeFixedConfirmed"] is True
        assert evidence["mcuBootId"] == case.start["targetMcuBootId"]
        assert any(
            row["event_type"] == "DEVICE_FAULT_RECOVERED"
            and json.loads(row["payload_json"])["payload"]["faultUid"]
            == status["faultUid"]
            for row in case.store.list_pending_events()
        )
        with pytest.raises(JobSafetyError) as stale:
            owner.confirm_communication_fault_recovered({
                "expectedFaultUid": status["faultUid"],
                "reason": "重复旧操作",
                "causeFixedConfirmed": True,
            })
        assert stale.value.code == "FAULT_IDENTITY_CHANGED"


def test_operator_cannot_clear_communication_fault_without_confirmation(
    runtime,
    tmp_path,
):
    with dropped_start_decision(runtime, tmp_path) as (case, owner):
        wait_for_control_failure(case, owner)
        status = owner.communication_fault_status()
        with pytest.raises(JobSafetyError) as missing:
            owner.confirm_communication_fault_recovered({
                "expectedFaultUid": status["faultUid"],
                "reason": "尚未确认原因已经排除",
                "causeFixedConfirmed": False,
            })
        assert missing.value.code == "MANUAL_CONFIRMATION_REQUIRED"
        assert case.store.get_state(
            "native_blocking_fault"
        ) == "MCU_COMMUNICATION_UNAVAILABLE"


def test_manual_communication_recovery_rolls_back_latch_if_event_write_fails(
    runtime,
    tmp_path,
    monkeypatch,
):
    with dropped_start_decision(runtime, tmp_path) as (case, owner):
        wait_for_control_failure(case, owner)
        status = owner.communication_fault_status()

        def fail_event(*_args, **_kwargs):
            raise RuntimeError("injected recovery event failure")

        monkeypatch.setattr(case.store, "_insert_event", fail_event)
        with pytest.raises(RuntimeError, match="recovery event failure"):
            owner.confirm_communication_fault_recovered({
                "expectedFaultUid": status["faultUid"],
                "reason": "现场已修复，但模拟事件落盘失败",
                "causeFixedConfirmed": True,
            })

        assert case.store.get_state(
            "native_blocking_fault"
        ) == "MCU_COMMUNICATION_UNAVAILABLE"
        assert case.store.get_active_edge_fault(
            "UART", "UART_PROTOCOL"
        )["fault_uid"] == status["faultUid"]


def test_late_complete_result_after_control_failure_is_raw_evidence_only(runtime, tmp_path):
    with dropped_start_decision(runtime, tmp_path) as (case, owner):
        wait_for_control_failure(case, owner)
        failed = case.store.get_command(case.business["commandUid"])
        old_events = matching_observations(case, case.business["commandUid"])
        # Continue the already-running autonomous MCU only after the Pi has
        # closed its business. Its real late result must remain raw evidence.
        now = tick(runtime, case.wire.now, 250)
        now = take_samples(runtime, [700] * 5, start=now, measurement=3)
        runtime[0].McuWorkPreparation_Poll(runtime[2], runtime[1], now)
        now = finish_delivery_round(runtime, case, now, 4, 1000)
        assert select(runtime, case.delivery, now, "END")
        case.wire.now = now
        frame = held_result_frame(case)
        decoded = uart.decode_frame(frame, sender_role="MCU")
        result = uart.decode_payload("WORK_RESULT", decoded["payload"])
        case.serial.rx.extend(frame)
        owner.poll()
        saved = case.store.get_native_mcu_result(result["mcuBootId"], result["resultSequence"])
        assert saved is not None and saved["payload"] == decoded["payload"]
        assert case.store.get_native_result_report(case.permit, case.uid, device_name="device-1") is None
        assert case.store.get_work_slot() is None
        assert case.store.get_command(case.business["commandUid"]) == failed
        assert matching_observations(case, case.business["commandUid"]) == old_events
        assert case.safety.get_job_permit(case.permit.permit_uid)["completionOutcome"] == "FAILED"


@pytest.mark.parametrize("permanent_state", ["GRANTED", "ACTIVE"])
def test_pi_restart_closes_unwritten_start_against_exact_permanent_state(runtime, tmp_path, permanent_state):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        business = start_command()
        assert case.store.receive_command(business["commandUid"], business["commandType"], business) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == business["commandUid"]
        uid = owner.start_delivery_command(business)["mcu_command_uid"]
        permit = case.safety.request_job(business, work_type="DELIVERY",
            work_uid=business["payload"]["sessionUid"])
        if permanent_state == "ACTIVE":
            case.safety.begin_job(permit, begin_uid=permit.work_uid, digest=permit.request_digest_sha256)
        assert case.store.get_native_command(uid)["write_claimed"] == 0
        owner.close()
        case.store.close()
        case.store.initialize()
        restarted = open_owner(case, case.clock)
        try:
            poll_until(restarted, case.clock, lambda: case.store.get_work_slot() is None)
            command = case.store.get_command(business["commandUid"])
            marker = command["result"]["nativeControlFailure"]
            assert command["state"] == "FAILED" and marker["state"] == "APPLIED"
            assert marker["evidence"]["reason"] == "EDGE_RESTARTED_BEFORE_START"
            remote = case.safety.get_job_permit(permit.permit_uid)
            if permanent_state == "GRANTED":
                assert remote["state"] == "ABANDONED"
                assert remote["dispositionUid"] == permit.command_uid
                assert remote["abandonEvidenceSha256"] == marker["evidenceSha256"]
            else:
                assert remote["state"] == "COMPLETED"
                assert remote["completionOutcome"] == "FAILED"
                assert remote["completionDigestSha256"] == marker["evidenceSha256"]
            assert not any(frame["messageName"] == "START_DELIVERY_SESSION" for frame in case.wire.sent
                if uart.decode_payload(frame["messageName"], frame["payload"]).get("mcuCommandUid") == uid)
            assert case.store.get_state("native_blocking_fault") in {None, ""}
        finally:
            restarted.close()


def test_pi_restart_after_failed_permanent_commit_finishes_same_local_failure(runtime, tmp_path, monkeypatch):
    with dropped_start_decision(runtime, tmp_path) as (case, owner):
        original_apply = case.store.apply_native_control_failure
        crossed = []
        def crash_before_apply(*args, **kwargs):
            assert kwargs["permit_snapshot"]["completionOutcome"] == "FAILED"
            crossed.append(kwargs["permit_snapshot"])
            raise RuntimeError("injected Pi crash after control-failure ledger commit")
        monkeypatch.setattr(case.store, "apply_native_control_failure", crash_before_apply)
        with pytest.raises(RuntimeError, match="after control-failure ledger commit"):
            for _ in range(160):
                case.clock.now += 100
                owner.poll()
                time.sleep(0.001)
        assert len(crossed) == 1
        command = case.store.get_command(case.business["commandUid"])
        assert command["result"]["nativeControlFailure"]["state"] == "PREPARED"
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        permanent = case.safety.get_job_permit(case.permit.permit_uid)
        assert permanent["state"] == "COMPLETED" and permanent["completionOutcome"] == "FAILED"
        monkeypatch.setattr(case.store, "apply_native_control_failure", original_apply)
        sent = len(case.wire.sent)
        owner.close()
        case.store.close()
        case.store.initialize()
        restarted = open_owner(case, case.clock)
        try:
            poll_until(restarted, case.clock, lambda: case.store.get_work_slot() is None)
            after = case.store.get_command(case.business["commandUid"])
            assert after["result"]["nativeControlFailure"]["state"] == "APPLIED"
            assert case.safety.get_job_permit(case.permit.permit_uid) == permanent
            assert len(matching_observations(case, case.business["commandUid"])) == 1
            assert not any(frame["messageName"] == "START_DELIVERY_SESSION" for frame in case.wire.sent[sent:]
                if uart.decode_payload(frame["messageName"], frame["payload"]).get("mcuCommandUid") == case.uid)
        finally:
            restarted.close()
