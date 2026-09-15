"""Native START-response timeout closes one work without replay or settlement."""
from contextlib import contextmanager
import json
import time

import pytest

from job_safety import JobPermit, JobSafetyError, command_request_digest
from onenet_wire import canonical_payload_sha256
from hardware.tests.test_mcu_work_preparation import take_samples
from hardware.tests.test_mcu_simplified_execution import library, runtime, select, tick
from hardware.tests.test_native_business_runtime import (
    apply_configuration, await_start_facts, completed_first_work, open_owner, poll_until, start_command,
)
from hardware.tests.test_native_runtime_configuration_reload import held_result_frame
from hardware.tests.test_simplified_mcu_pi_business import finish_delivery_round
from uart_request_tracker import RequestReplyEvent
import uart2_protocol as uart


def matching_observations(case, command_uid):
    return [json.loads(row["payload_json"]) for row in case.store.list_pending_events()
        if row["event_type"] == "DEVICE_COMMAND_OBSERVED"
        and json.loads(row["payload_json"])["commandUid"] == command_uid]


def delivery_for_bag(bag_uid):
    command = start_command()
    command["payload"]["bagUid"] = bag_uid
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    return command


@contextmanager
def dropped_start_decision(
    runtime,
    tmp_path,
    *,
    clean=False,
    no_old_bag=False,
):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        business = start_command(clean)
        if no_old_bag:
            assert clean
            business["payload"]["oldBagUid"] = None
            business["payloadSha256"] = canonical_payload_sha256(
                business["payload"]
            )
        assert case.store.receive_command(business["commandUid"], business["commandType"], business) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == business["commandUid"]
        pending = (
            owner.start_clean_command(business)
            if clean
            else owner.start_delivery_command(business)
        )
        uid = pending["mcu_command_uid"]
        case.serial.drop = lambda decoded: (decoded["messageName"] in {"COMMAND_DECISION", "COMMAND_QUERY_RESULT"}
            and uart.decode_payload(decoded["messageName"], decoded["payload"])["mcuCommandUid"] == uid)
        poll_until(owner, case.clock, lambda: case.store.get_native_command(uid)["write_claimed"])
        case.business, case.uid = business, uid
        case.start = uart.decode_payload(
            business["commandType"],
            case.store.get_native_command(uid)["payload"],
        )
        work_key = "operationUid" if clean else "sessionUid"
        case.permit = JobPermit(
            business["commandUid"],
            business["payload"][work_key],
            business["commandUid"],
            "CLEAN" if clean else "DELIVERY",
            command_request_digest(business),
        )
        yield case, owner


def wait_for_control_failure(case, owner):
    # Valid DEVICE_FACTS replies do not satisfy the independent START wait.
    for _ in range(150):
        case.clock.now += 100
        owner.poll()
        if case.store.get_work_slot() is None:
            return
        time.sleep(0.001)
    raise AssertionError("control failure did not release its exact local work")


def install_historical_manual_fault(case, owner):
    """Model an automaticRecovery=false fault written by an older runtime."""
    case.store.set_state(
        "native_blocking_fault",
        "MCU_COMMUNICATION_UNAVAILABLE",
    )
    assert case.store.observe_fault_and_create_event(
        device_name="device-1",
        component="UART",
        fault_code="UART_PROTOCOL",
        severity="BLOCK_DEVICE",
        mcu_boot_id=case.start["targetMcuBootId"],
        detail={
            "profile": "native-control-communication-v1",
            "reasonCode": "MCU_COMMUNICATION_UNAVAILABLE",
            "automaticRecovery": False,
        },
    ) in {"ACCEPTED", "DUPLICATE"}
    status = owner.communication_fault_status()
    assert status["faultUid"]
    return status


def save_start_rejection(case, reason):
    record = case.store.get_native_command(case.uid)
    values = uart.decode_payload(record["message_name"], record["payload"])
    payload = uart.encode_payload(
        "COMMAND_DECISION",
        {
            key: values[key]
            for key in (
                "mcuCommandUid",
                "commandDigestSha256",
                "targetMcuBootId",
                "commandSequence",
            )
        }
        | {
            "currentMcuBootId": values["targetMcuBootId"],
            "outcome": "REJECTED",
            "errorCode": reason,
        },
    )
    assert case.store.save_native_command_observation(
        "COMMAND_DECISION",
        payload,
    )


@pytest.mark.parametrize("clean", [False, True])
def test_explicit_start_rejection_closes_original_work_without_device_latch_or_clean_interlock(
    runtime,
    tmp_path,
    clean,
):
    with dropped_start_decision(runtime, tmp_path, clean=clean) as (case, owner):
        save_start_rejection(case, "BUSY")
        poll_until(owner, case.clock, lambda: case.store.get_work_slot() is None)

        command = case.store.get_command(case.business["commandUid"])
        marker = command["result"]["nativeControlFailure"]
        assert command["state"] == "REJECTED"
        assert command["last_error"] == "BUSY"
        assert marker["state"] == "APPLIED"
        assert marker["evidence"]["stage"] == "REJECTED"
        assert marker["evidence"]["reason"] == "BUSY"
        assert marker["evidence"]["businessValue"] == "NONE"
        assert case.safety.get_job_permit(case.permit.permit_uid)[
            "completionOutcome"
        ] == "FAILED"
        assert case.store.get_state("native_blocking_fault") in {None, ""}
        assert not case.store.clean_restart_interlock_active(1)
        assert len(matching_observations(case, case.business["commandUid"])) == 1


def test_unanswered_start_fails_permanently_but_later_exact_query_recovers_communication(runtime, tmp_path):
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
        assert case.store.get_active_edge_fault("UART", "UART_PROTOCOL") is None
        assert case.store.get_state("native_blocking_fault") == ""
        fault = case.store._conn.execute(
            """SELECT * FROM edge_fault_state
               WHERE component='UART' AND fault_code='UART_PROTOCOL'
               ORDER BY last_detected_at DESC, rowid DESC LIMIT 1"""
        ).fetchone()
        assert fault["lifecycle"] == "RECOVERED"
        assert json.loads(fault["detail_json"]) == dict(
            profile="native-control-communication-v1",
            reasonCode="MCU_COMMUNICATION_UNAVAILABLE",
            automaticRecovery=True,
        )
        assert json.loads(fault["recovery_evidence"])["profile"] == (
            "native-control-communication-auto-recovery-v1"
        )
        assert len(case.store.list_native_result_report_tasks()) == 1  # Only the fixture's completed work.
        with pytest.raises(JobSafetyError) as blocked:
            owner.start_delivery_command(start_command())
        assert blocked.value.code == "DEVICE_BUSY"


def test_short_start_write_fails_original_business_without_mcu_timeout_fault(
    runtime,
    tmp_path,
):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        business = start_command()
        assert case.store.receive_command(
            business["commandUid"],
            business["commandType"],
            business,
        ) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == business[
            "commandUid"
        ]
        uid = owner.start_delivery_command(business)["mcu_command_uid"]
        real_write = case.serial.write

        def short_write(frame):
            decoded = uart.decode_frame(frame, sender_role="EDGE")
            if decoded["messageName"] == "START_DELIVERY_SESSION":
                values = uart.decode_payload(
                    decoded["messageName"],
                    decoded["payload"],
                )
                if values["mcuCommandUid"] == uid:
                    return len(frame) - 1
            return real_write(frame)

        case.serial.write = short_write
        poll_until(owner, case.clock, lambda: case.store.get_work_slot() is None)

        record = case.store.get_native_command(uid)
        assert record["write_claimed"] and record["decision_outcome"] is None
        command = case.store.get_command(business["commandUid"])
        marker = command["result"]["nativeControlFailure"]
        assert command["state"] == "FAILED"
        assert command["last_error"] == "UART_SHORT_WRITE"
        assert marker["state"] == "APPLIED"
        assert marker["evidence"]["reason"] == "UART_SHORT_WRITE"
        assert marker["evidence"]["businessValue"] == "NONE"
        permanent = case.safety.get_job_permit(business["commandUid"])
        assert permanent["state"] == "COMPLETED"
        assert permanent["completionOutcome"] == "FAILED"
        assert case.store.get_active_edge_fault("UART", "UART_PROTOCOL") is None
        assert case.store.get_state("native_blocking_fault") in {None, ""}


def test_exact_post_fault_reply_recovers_before_failed_work_slot_release(
    runtime,
    tmp_path,
    monkeypatch,
):
    with dropped_start_decision(runtime, tmp_path) as (case, owner):
        original = owner._complete_control_failure
        monkeypatch.setattr(owner, "_complete_control_failure", lambda *_: None)
        for _ in range(100):
            case.clock.now += 100
            owner.poll()
            if case.store.get_active_edge_fault("UART", "UART_PROTOCOL"):
                break
        fault = case.store.get_active_edge_fault("UART", "UART_PROTOCOL")
        assert fault is not None
        assert case.store.get_work_slot() is not None

        # A non-late reply to an unrelated request that was already in flight
        # before this fault cannot prove post-fault recovery.
        boundary = owner._communication_fault_request_boundary
        owner._recover_communication_from_matches([
            RequestReplyEvent(
                request_name="QUERY_DEVICE_FACTS",
                reply_name="DEVICE_FACTS_REPLY",
                channel="query:QUERY_DEVICE_FACTS",
                identity={},
                written_at_ms=case.clock.now - 1,
                observed_at_ms=case.clock.now,
                request_sequence=boundary,
                critical=False,
                reply={"currentMcuBootId": case.start["targetMcuBootId"]},
            )
        ])
        assert case.store.get_active_edge_fault("UART", "UART_PROTOCOL") is not None

        for _ in range(20):
            case.clock.now += 100
            owner.poll()
            if case.store.get_active_edge_fault("UART", "UART_PROTOCOL") is None:
                break
        assert case.store.get_active_edge_fault("UART", "UART_PROTOCOL") is None
        assert case.store.get_state("native_blocking_fault") == ""
        assert case.store.get_work_slot() is not None
        failed = case.store.get_command(case.business["commandUid"])
        assert failed["state"] == "FAILED"
        assert failed["result"]["nativeControlFailure"]["state"] == "PREPARED"

        monkeypatch.setattr(owner, "_complete_control_failure", original)
        wait_for_control_failure(case, owner)
        assert case.store.get_command(case.business["commandUid"])[
            "state"
        ] == "FAILED"


def test_new_pi_process_recovers_old_auto_fault_only_from_new_exact_reply(
    runtime,
    tmp_path,
):
    with dropped_start_decision(runtime, tmp_path) as (case, owner):
        case.serial.drop = lambda _decoded: True
        wait_for_control_failure(case, owner)
        fault = case.store.get_active_edge_fault("UART", "UART_PROTOCOL")
        assert fault is not None
        assert json.loads(fault["detail_json"])["automaticRecovery"] is True
        failed_before = case.store.get_command(case.business["commandUid"])
        owner.close()

        restarted = open_owner(case, case.clock)
        try:
            assert restarted.transport.requests.last_matched_ms is None
            poll_until(
                restarted,
                case.clock,
                lambda: case.store.get_active_edge_fault(
                    "UART", "UART_PROTOCOL"
                ) is None,
            )
            assert case.store.get_state("native_blocking_fault") == ""
            assert case.store.get_command(case.business["commandUid"])[
                "result"
            ] == failed_before["result"]
            assert case.store.get_command(case.business["commandUid"])[
                "state"
            ] == "FAILED"
        finally:
            restarted.close()


def test_unanswered_clean_start_latches_bag_confirmation_after_failure(
    runtime,
    tmp_path,
):
    with dropped_start_decision(runtime, tmp_path, clean=True) as (
        case,
        owner,
    ):
        wait_for_control_failure(case, owner)

        failed = case.store.get_command(case.business["commandUid"])
        assert failed["state"] == "FAILED"
        assert failed["last_error"] == "MCU_COMMUNICATION_UNAVAILABLE"
        assert case.store.clean_restart_interlock_active(1)
        assert case.store.get_clean_restart_interlock_metadata(1) == {
            "profile": "native-clean-bag-interlock-v1",
            "sourceWorkUid": case.business["payload"]["operationUid"],
            "portNo": 1,
            "oldBagUid": case.business["payload"]["oldBagUid"],
            "newBagUid": case.business["payload"]["newBagUid"],
            "sourceCommandUid": case.business["commandUid"],
        }

        assert case.store.get_state("native_blocking_fault") == ""
        assert case.store.clean_restart_interlock_active(1)

        mismatch = delivery_for_bag("77777777-7777-4777-8777-777777777777")
        assert case.store.receive_command(
            mismatch["commandUid"],
            mismatch["commandType"],
            mismatch,
        ) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == (
            mismatch["commandUid"]
        )
        with pytest.raises(JobSafetyError) as blocked:
            owner.start_delivery_command(mismatch)
        assert blocked.value.code == "CLEAN_BAG_CONFIRMATION_REQUIRED"
        assert case.store.clean_restart_interlock_active(1)


def test_clean_failure_preserves_authoritative_null_when_no_old_bag(
    runtime,
    tmp_path,
):
    with dropped_start_decision(
        runtime,
        tmp_path,
        clean=True,
        no_old_bag=True,
    ) as (case, owner):
        wait_for_control_failure(case, owner)

        assert case.store.get_clean_restart_interlock_metadata(1) == {
            "profile": "native-clean-bag-interlock-v1",
            "sourceWorkUid": case.business["payload"]["operationUid"],
            "portNo": 1,
            "oldBagUid": None,
            "newBagUid": case.business["payload"]["newBagUid"],
            "sourceCommandUid": case.business["commandUid"],
        }


@pytest.mark.parametrize(
    ("old_bag_uid", "confirmed_bag_field"),
    [
        ("88888888-8888-4888-8888-888888888888", "oldBagUid"),
        ("88888888-8888-4888-8888-888888888888", "newBagUid"),
        (None, "newBagUid"),
    ],
)
def test_matching_backend_start_atomically_takes_slot_and_clears_structured_bag_interlock(
    runtime,
    tmp_path,
    old_bag_uid,
    confirmed_bag_field,
):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        metadata = {
            "profile": "native-clean-bag-interlock-v1",
            "sourceWorkUid": "11111111-1111-4111-8111-111111111111",
            "portNo": 1,
            "oldBagUid": old_bag_uid,
            "newBagUid": "99999999-9999-4999-8999-999999999999",
            "sourceCommandUid": "22222222-2222-4222-8222-222222222222",
        }
        with case.store.transaction():
            case.store._set_clean_restart_interlock_in_tx(
                case.store._conn,
                1,
                True,
                metadata=metadata,
            )
        next_start = delivery_for_bag(metadata[confirmed_bag_field])
        assert case.store.receive_command(
            next_start["commandUid"],
            next_start["commandType"],
            next_start,
        ) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == (
            next_start["commandUid"]
        )

        owner.start_delivery_command(next_start)

        slot = case.store.get_work_slot()
        assert slot["work_uid"] == next_start["payload"]["sessionUid"]
        assert slot["context"]["start_bag_uid"] == (
            metadata[confirmed_bag_field]
        )
        assert not case.store.clean_restart_interlock_active(1)
        assert case.store.get_clean_restart_interlock_metadata(1) is None


def test_legacy_boolean_clean_interlock_never_auto_clears_for_new_start(
    runtime,
    tmp_path,
):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        with case.store.transaction():
            case.store._set_clean_restart_interlock_in_tx(
                case.store._conn,
                1,
                True,
            )
        command = start_command()
        assert case.store.receive_command(
            command["commandUid"],
            command["commandType"],
            command,
        ) == "ACCEPTED"
        assert case.store.claim_next_command()["command_uid"] == (
            command["commandUid"]
        )

        with pytest.raises(JobSafetyError) as blocked:
            owner.start_delivery_command(command)

        assert blocked.value.code == "CLEAN_BAG_CONFIRMATION_REQUIRED"
        assert case.store.get_state(
            case.store._clean_restart_interlock_key(1)
        ) == "true"
        assert case.store.get_work_slot() is None


def test_structured_interlock_rejects_an_unpersisted_matching_start_and_old_clear_api(
    runtime,
    tmp_path,
):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        command = start_command()
        metadata = {
            "profile": "native-clean-bag-interlock-v1",
            "sourceWorkUid": "11111111-1111-4111-8111-111111111111",
            "portNo": 1,
            "oldBagUid": command["payload"]["bagUid"],
            "newBagUid": "99999999-9999-4999-8999-999999999999",
            "sourceCommandUid": "22222222-2222-4222-8222-222222222222",
        }
        with case.store.transaction():
            case.store._set_clean_restart_interlock_in_tx(
                case.store._conn,
                1,
                True,
                metadata=metadata,
            )

        with pytest.raises(JobSafetyError) as blocked:
            owner.start_delivery_command(command)
        assert blocked.value.code == "CLEAN_BAG_CONFIRMATION_REQUIRED"
        assert case.store.get_work_slot() is None

        case.store.clear_clean_restart_interlock(1)
        assert case.store.clean_restart_interlock_active(1)
        assert case.store.get_clean_restart_interlock_metadata(1) == metadata


def test_clean_failure_does_not_freeze_without_atomic_bag_interlock(
    runtime,
    tmp_path,
    monkeypatch,
):
    with dropped_start_decision(runtime, tmp_path, clean=True) as (
        case,
        owner,
    ):
        original = case.store._set_clean_restart_interlock_in_tx

        def fail_interlock(*_args, **_kwargs):
            raise RuntimeError("injected clean bag interlock failure")

        monkeypatch.setattr(
            case.store,
            "_set_clean_restart_interlock_in_tx",
            fail_interlock,
        )
        with pytest.raises(RuntimeError, match="bag interlock failure"):
            for _ in range(150):
                case.clock.now += 100
                owner.poll()
                time.sleep(0.001)

        command = case.store.get_command(case.business["commandUid"])
        assert command["state"] == "PROCESSING"
        assert case.store.get_work_slot()["work_uid"] == (
            case.permit.work_uid
        )
        assert not case.store.clean_restart_interlock_active(1)
        assert matching_observations(
            case,
            case.business["commandUid"],
        ) == []
        monkeypatch.setattr(
            case.store,
            "_set_clean_restart_interlock_in_tx",
            original,
        )


def test_operator_clears_exact_communication_fault_only_after_fresh_reply(
    runtime,
    tmp_path,
):
    with dropped_start_decision(runtime, tmp_path) as (case, owner):
        wait_for_control_failure(case, owner)
        status = install_historical_manual_fault(case, owner)
        assert status == {
            "reasonCode": "MCU_COMMUNICATION_UNAVAILABLE",
            "faultUid": status["faultUid"],
            "mcuBootId": case.start["targetMcuBootId"],
            "freshCommunicationConfirmed": True,
            "activeWorkUid": None,
            "manualRecoveryEligible": True,
        }

        result = owner.confirm_communication_fault_recovered({
            "expectedFaultUid": status["faultUid"],
            "reason": "现场修复串口连接并确认设备事实查询已恢复",
            "causeFixedConfirmed": True,
        })
        assert result["disposition"] == "RECOVERED"
        assert case.store.get_state("native_blocking_fault") == ""
        fault = case.store._conn.execute(
            "SELECT * FROM edge_fault_state WHERE fault_uid=?",
            (status["faultUid"],),
        ).fetchone()
        assert fault["lifecycle"] == "RECOVERED"
        evidence = json.loads(fault["recovery_evidence"])
        assert evidence["profile"] == (
            "native-control-communication-manual-recovery-v1"
        )
        with pytest.raises(JobSafetyError) as stale:
            owner.confirm_communication_fault_recovered({
                "expectedFaultUid": status["faultUid"],
                "reason": "重复旧操作",
                "causeFixedConfirmed": True,
            })
        assert stale.value.code == "FAULT_IDENTITY_CHANGED"


def test_operator_cannot_clear_fault_from_an_expired_foreground_snapshot(
    runtime,
    tmp_path,
):
    with dropped_start_decision(runtime, tmp_path) as (case, owner):
        wait_for_control_failure(case, owner)
        fresh = install_historical_manual_fault(case, owner)
        assert fresh["freshCommunicationConfirmed"] is True
        case.clock.now += 10_001
        expired = owner.communication_fault_status()
        assert expired["mcuBootId"] is None
        assert expired["freshCommunicationConfirmed"] is False
        assert expired["manualRecoveryEligible"] is False
        with pytest.raises(JobSafetyError) as blocked:
            owner.confirm_communication_fault_recovered({
                "expectedFaultUid": fresh["faultUid"],
                "reason": "前台轮询未恢复，旧快照不能作为通信恢复证据",
                "causeFixedConfirmed": True,
            })
        assert blocked.value.code == "MCU_COMMUNICATION_UNAVAILABLE"


def test_operator_cannot_clear_communication_fault_without_confirmation(
    runtime,
    tmp_path,
):
    with dropped_start_decision(runtime, tmp_path) as (case, owner):
        wait_for_control_failure(case, owner)
        status = install_historical_manual_fault(case, owner)
        with pytest.raises(JobSafetyError) as missing:
            owner.confirm_communication_fault_recovered({
                "expectedFaultUid": status["faultUid"],
                "reason": "尚未确认原因已经排除",
                "causeFixedConfirmed": False,
            })
        assert missing.value.code == "MANUAL_CONFIRMATION_REQUIRED"


def test_manual_communication_recovery_rolls_back_latch_if_event_write_fails(
    runtime,
    tmp_path,
    monkeypatch,
):
    with dropped_start_decision(runtime, tmp_path) as (case, owner):
        wait_for_control_failure(case, owner)
        status = install_historical_manual_fault(case, owner)

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
