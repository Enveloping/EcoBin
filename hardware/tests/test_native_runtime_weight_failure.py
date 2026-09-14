"""Actual C terminal-scale failure, Pi custody and later fresh-read admission.

Only the serial wire and updater socket are in-process boundaries. Final result
bytes and later idle-scale observations come from the compiled MCU producer.
No connected device, live backend or physical actuator is used.
"""
from contextlib import contextmanager
import json
import time

import pytest

from job_safety import JobSafetyError
from native_business_completion import MARKER
from hardware.tests.test_native_runtime_configuration_reload import (
    library, runtime, actual_idle_weight, start_and_measure_delivery, held_result_frame, close_owner,
)
from hardware.tests.test_native_business_runtime import (
    completed_first_work, apply_configuration, await_start_facts, poll_until, start_command, open_owner,
)
from hardware.tests.test_mcu_simplified_execution import tick, select
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_native_business_completion import put_baseline
from hardware.tests.test_native_result_confirmation import confirmation_wire
from hardware.tests.test_simplified_mcu_pi_business import finish_delivery_round
from hardware.tests.test_native_runtime_rpc import DelayedUpdaterClient
import uart2_protocol as uart


def scale_fault(case):
    return case.store.get_active_edge_fault("WEIGHT_SENSOR", "WEIGHT_SENSOR", 1)


def fault_events(case, fault_uid, event_type):
    return [json.loads(row["payload_json"]) for row in case.store.list_pending_events()
        if row["event_type"] == event_type
        and json.loads(row["payload_json"])["payload"]["faultUid"] == fault_uid]


def publish_idle_scale_fact(owner, case, *, now, captured, status="VALID"):
    facts = dict(owner._facts)
    facts.update(
        status="AVAILABLE",
        appliedConfigVersion=max(1, facts["appliedConfigVersion"]),
        capturedUptimeMs=captured,
        scaleCapturedUptimeMs=captured - 1_000,
        scaleAttemptSequence=facts["scaleAttemptSequence"] + 1,
        scaleReadStatus=status,
    )
    owner._facts = facts
    owner._facts_requested_at = now
    case.clock.now = now
    return facts


def test_one_valid_but_stale_idle_sample_does_not_flash_scale_fault(
    runtime, tmp_path
):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        await_start_facts(case, owner)
        started = case.clock.now
        stale = publish_idle_scale_fact(
            owner,
            case,
            now=started,
            captured=20_000,
        )
        owner._scale_health_poll(started)
        assert scale_fault(case) is None

        fresh = dict(stale)
        fresh.update(
            capturedUptimeMs=20_250,
            scaleCapturedUptimeMs=20_250,
            scaleAttemptSequence=stale["scaleAttemptSequence"] + 1,
        )
        case.clock.now = started + 250
        owner._facts = fresh
        owner._facts_requested_at = case.clock.now
        owner._scale_health_poll(case.clock.now)
        assert owner._scale_wait is None
        assert not any(
            row["event_type"] == "DEVICE_FAULT_OBSERVED"
            and json.loads(row["payload_json"])["payload"].get("component")
            == "WEIGHT_SENSOR"
            for row in case.store.list_pending_events()
        )


def test_valid_scale_sample_stale_for_five_seconds_becomes_real_fault(
    runtime, tmp_path
):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        await_start_facts(case, owner)
        started = case.clock.now
        for elapsed in (0, 4_999):
            publish_idle_scale_fact(
                owner,
                case,
                now=started + elapsed,
                captured=30_000 + elapsed,
            )
            owner._scale_health_poll(case.clock.now)
            assert scale_fault(case) is None

        publish_idle_scale_fact(
            owner,
            case,
            now=started + 5_000,
            captured=35_000,
        )
        owner._scale_health_poll(case.clock.now)
        fault = scale_fault(case)
        assert fault is not None
        assert json.loads(fault["detail_json"])["readStatus"] == "VALID"


@contextmanager
def terminal_failure(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        apply_configuration(case, owner)
        await_start_facts(case, owner)
        case.business, case.uid = start_and_measure_delivery(runtime, case, owner, 3)
        case.baseline = put_baseline(case.store, case.business["payload"]["bagUid"], grams=83)
        now = tick(runtime, case.wire.now, 100)
        now = tick(runtime, now, case.start["deliveryAutoCloseMs"])
        now = tick(runtime, now, 100)
        now = tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
        reader = runtime[0].TestPreparation_Weight(runtime[2])
        # Twenty actual requests receive no scale frame. Each request expires
        # through McuWeightRun_Poll; the measurement's five-second deadline
        # freezes UNAVAILABLE, not a fabricated zero or old initial sample.
        for _ in range(20):
            assert runtime[0].McuWeightRun_StartOwnedAttempt(reader, now)
            now = tick(runtime, now, 250)
        case.wire.now = now
        frame = held_result_frame(case)
        case.frame = frame
        case.raw = uart.decode_frame(frame)["payload"]
        case.result = uart.decode_payload("WORK_RESULT", case.raw)
        assert case.result["finishReason"] == "FAILED"
        assert (case.result["initialKind"], case.result["initialWeightGrams"]) == ("STABLE_MEAN", 700)
        assert case.result["finalKind"] == "UNAVAILABLE"
        assert case.result["finalElapsedMs"] == 5000 and case.result["finalSampleCount"] == 0
        assert case.result["finalFaultCode"] == "WEIGHT_TIMEOUT" and case.result["deliveryRoundCount"] == 1
        # Let normal runtime query the still-retained C result again, not a
        # direct test-side EdgeStore save or a mock result reporter.
        poll_until(owner, case.clock, lambda: case.store.get_native_result_report(
            case.permit, case.uid, device_name="device-1") is not None)
        case.report = case.store.get_native_result_report(case.permit, case.uid, device_name="device-1")
        case.event = json.loads(case.store.get_event(case.report["eventUid"])["payload_json"])
        yield case, owner


def confirm_failure(case, owner):
    _, _, confirmation = confirmation_wire(case, case.report["eventUid"])
    assert case.store.receive_business_confirmation_and_create_receipt(
        command=confirmation, device_name="device-1") == "ACCEPTED"
    poll_until(owner, case.clock, lambda: case.store.get_work_slot() is None)
    return confirmation


def test_actual_five_second_final_timeout_reports_null_net_and_closes_failed_after_exact_confirmation(runtime, tmp_path):
    with terminal_failure(runtime, tmp_path) as (case, owner):
        saved = case.store.get_native_mcu_result(case.result["mcuBootId"], case.result["resultSequence"])
        assert saved["payload"] == case.raw
        payload = case.event["payload"]
        assert case.event["eventType"] == "DELIVERY_COMPLETE"
        assert payload["completionReason"] == "TERMINAL_WEIGHT_FAILURE"
        assert payload["deliveryNetWeightGrams"] is None
        assert payload["finalPostCloseMeasurement"]["reportedWeightGrams"] is None
        assert payload["firstPreOpenMeasurement"]["reportedWeightGrams"] == 700
        assert payload["finalDoorCommand"] == dict(command="CLOSE", outputStatus="COMMAND_DISPATCHED",
            physicalStateBasis="NOT_OBSERVABLE")
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None
        assert not case.store.list_native_work_actuator_events(case.permit.work_uid)
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        row = case.store.get_event(case.report["eventUid"])
        case.store.record_event_platform_reply(row["edge_event_sequence"], 200)
        for _ in range(20):
            owner.poll()
            case.clock.now += 10
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        confirm_failure(case, owner)
        command = case.store.get_command(case.business["commandUid"])
        assert command["state"] == "FAILED" and command["last_error"] == "WEIGHT_TIMEOUT"
        assert command["result"][MARKER]["state"] == "APPLIED"
        permanent = case.safety.get_job_permit(case.permit.permit_uid)
        assert permanent["state"] == "COMPLETED" and permanent["completionOutcome"] == "FAILED"
        assert case.store.get_bag_baseline(case.baseline["bag_uid"]) == case.baseline
        assert case.store.get_native_mcu_result(case.result["mcuBootId"], case.result["resultSequence"])["payload"] == case.raw


def test_real_fresh_idle_read_only_reopens_new_work_and_never_rewrites_old_failure(runtime, tmp_path):
    with terminal_failure(runtime, tmp_path) as (case, owner):
        confirm_failure(case, owner)
        poll_until(owner, case.clock, lambda: scale_fault(case) is not None)
        fault = scale_fault(case)
        assert fault["severity"] == "BLOCK_PORT" and fault["lifecycle"] == "OBSERVED"
        assert json.loads(fault["detail_json"])["readStatus"] == "TIMEOUT"
        assert len(fault_events(case, fault["fault_uid"], "DEVICE_FAULT_OBSERVED")) == 1
        assert any(row["fault_uid"] == fault["fault_uid"] for row in case.store.list_active_faults())
        old_permit, old_uid = case.permit, case.uid
        old_command = case.store.get_command(old_permit.command_uid)
        old_event = case.store.get_event(case.report["eventUid"])
        old_report = case.store.get_native_result_report(old_permit, old_uid, device_name="device-1")
        old_job = case.safety.get_job_permit(old_permit.permit_uid)
        sent = len(case.wire.sent)
        with pytest.raises(JobSafetyError, match="fresh actual scale"):
            owner.start_delivery_command(start_command())
        assert len(case.wire.sent) == sent and case.store.get_work_slot() is None
        actual_idle_weight(runtime, case, 710)
        poll_until(owner, case.clock, lambda: scale_fault(case) is None)
        assert len(fault_events(case, fault["fault_uid"], "DEVICE_FAULT_RECOVERED")) == 1
        business, uid = start_and_measure_delivery(runtime, case, owner, 5)
        later_slot = case.store.get_work_slot()
        # The original MCU bytes arriving again cannot complete or replace the
        # newly accepted business, even though their old report is confirmed.
        case.serial.rx.extend(case.frame)
        owner.poll()
        assert case.store.get_work_slot() == later_slot
        assert case.store.get_command(old_permit.command_uid) == old_command
        assert case.store.get_bag_baseline(case.baseline["bag_uid"]) == case.baseline
        now = finish_delivery_round(runtime, case, case.wire.now, 6, 1000)
        assert select(runtime, case.delivery, now, "END")
        case.wire.now = now
        poll_until(owner, case.clock, lambda: case.store.get_native_result_report(
            case.permit, uid, device_name="device-1") is not None)
        report = case.store.get_native_result_report(case.permit, uid, device_name="device-1")
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        assert event["payload"]["deliveryNetWeightGrams"] == 300
        _, _, confirmation = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(
            command=confirmation, device_name="device-1") == "ACCEPTED"
        poll_until(owner, case.clock, lambda: case.store.get_work_slot() is None)
        assert case.store.get_command(business["commandUid"])["state"] == "COMPLETED"
        assert case.store.get_command(old_permit.command_uid) == old_command
        assert case.store.get_event(old_report["eventUid"]) == old_event
        assert case.store.get_native_result_report(old_permit, old_uid, device_name="device-1") == old_report
        assert case.safety.get_job_permit(old_permit.permit_uid) == old_job
        assert case.store.get_bag_baseline(case.baseline["bag_uid"]) == case.baseline
        assert case.store.get_native_mcu_result(case.result["mcuBootId"], case.result["resultSequence"])["payload"] == case.raw


def test_new_valid_scale_read_does_not_clear_unrelated_manual_communication_stop(runtime, tmp_path):
    with terminal_failure(runtime, tmp_path) as (case, owner):
        confirm_failure(case, owner)
        poll_until(owner, case.clock, lambda: scale_fault(case) is not None)
        fault_uid = scale_fault(case)["fault_uid"]
        case.store.set_state("native_blocking_fault", "MCU_COMMUNICATION_UNAVAILABLE")
        actual_idle_weight(runtime, case, 710)
        poll_until(owner, case.clock, lambda: scale_fault(case) is None)
        assert len(fault_events(case, fault_uid, "DEVICE_FAULT_RECOVERED")) == 1
        assert case.store.get_state("native_blocking_fault") == "MCU_COMMUNICATION_UNAVAILABLE"
        sent = len(case.wire.sent)
        with pytest.raises(JobSafetyError, match="awaiting fault handling") as blocked:
            owner.start_delivery_command(start_command())
        assert blocked.value.code == "MCU_COMMUNICATION_UNAVAILABLE"
        assert len(case.wire.sent) == sent and case.store.get_work_slot() is None
        assert case.safety.get_job_permit(case.permit.permit_uid)["completionOutcome"] == "FAILED"
        assert case.store.get_command(case.business["commandUid"])["state"] == "FAILED"


@pytest.mark.parametrize("after_commit", [False, True])
def test_weight_failure_keeps_original_slot_until_actual_failed_ledger_receipt_arrives(runtime, tmp_path, after_commit):
    with terminal_failure(runtime, tmp_path) as (case, owner):
        client = DelayedUpdaterClient(case.safety._client.store)
        case.safety._client = client  # Delay the socket boundary; retain its real UpdaterStore.
        client.delay("COMPLETE_JOB", after_commit=after_commit)
        _, _, confirmation = confirmation_wire(case, case.report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(
            command=confirmation, device_name="device-1") == "ACCEPTED"
        try:
            poll_until(owner, case.clock, client.entered.is_set)
            assert not client.returned.is_set()
            before = len(case.wire.sent)
            for _ in range(111):
                owner.poll()
                assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
                assert case.store.get_bag_baseline(case.baseline["bag_uid"]) == case.baseline
                case.clock.now += 100
                time.sleep(0.001)
            assert not client.watchdog_fired.is_set()
            assert not case.store.get_state("native_blocking_fault")
            assert client.calls["COMPLETE_JOB"] == 1
            assert sum(frame["messageName"] == "QUERY_DEVICE_FACTS" for frame in case.wire.sent[before:]) >= 10
            permanent = client.store.get_job_permit({"permitUid": case.permit.permit_uid})
            assert permanent["state"] == ("COMPLETED" if after_commit else "ACTIVE")
            if after_commit:
                assert permanent["completionOutcome"] == "FAILED"
            command = case.store.get_command(case.business["commandUid"])
            assert command["state"] == "FAILED" and command["result"][MARKER]["state"] == "PREPARED"
            client.release.set()
            poll_until(owner, case.clock, lambda: case.store.get_work_slot() is None)
            assert case.safety.get_job_permit(case.permit.permit_uid)["completionOutcome"] == "FAILED"
            assert case.store.get_command(case.business["commandUid"])["result"][MARKER]["state"] == "APPLIED"
        finally:
            client.release.set()
            close_owner(owner)


def test_pi_restart_after_failed_ledger_commit_reuses_original_completion_and_releases_once(runtime, tmp_path, monkeypatch):
    with terminal_failure(runtime, tmp_path) as (case, owner):
        _, _, confirmation = confirmation_wire(case, case.report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(
            command=confirmation, device_name="device-1") == "ACCEPTED"
        original_apply = case.store.apply_native_business_completion
        crossed = []
        def crash_before_local_apply(*args, **kwargs):
            assert kwargs["permit_snapshot"]["completionOutcome"] == "FAILED"
            crossed.append(kwargs["permit_snapshot"])
            raise RuntimeError("injected Pi crash after actual FAILED ledger receipt")
        monkeypatch.setattr(case.store, "apply_native_business_completion", crash_before_local_apply)
        with pytest.raises(RuntimeError, match="after actual FAILED"):
            poll_until(owner, case.clock, lambda: False)
        assert len(crossed) == 1
        monkeypatch.setattr(case.store, "apply_native_business_completion", original_apply)
        before_job = case.safety.get_job_permit(case.permit.permit_uid)
        assert before_job["state"] == "COMPLETED" and before_job["completionOutcome"] == "FAILED"
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.store.get_command(case.business["commandUid"])["result"][MARKER]["state"] == "PREPARED"
        event = case.store.get_event(case.report["eventUid"])
        close_owner(owner)
        case.store.close()
        case.store.initialize()
        sent = len(case.wire.sent)
        restarted = open_owner(case, case.clock)
        try:
            poll_until(restarted, case.clock, lambda: case.store.get_work_slot() is None)
            after = case.store.get_command(case.business["commandUid"])
            assert after["state"] == "FAILED" and after["result"][MARKER]["state"] == "APPLIED"
            assert case.safety.get_job_permit(case.permit.permit_uid) == before_job
            assert case.store.get_event(case.report["eventUid"]) == event
            assert case.store.get_native_result_report(case.permit, case.uid, device_name="device-1") == case.report
            assert case.store.get_bag_baseline(case.baseline["bag_uid"]) == case.baseline
            assert not any(frame["messageName"] == "START_DELIVERY_SESSION" for frame in case.wire.sent[sent:])
            poll_until(restarted, case.clock, lambda: scale_fault(case) is not None)
            with pytest.raises(JobSafetyError, match="fresh actual scale"):
                restarted.start_delivery_command(start_command())
        finally:
            close_owner(restarted)
