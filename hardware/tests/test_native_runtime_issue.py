"""Actual MCU reboot -> diagnostic-only archive through the native entry.

The hardware endpoint is compiled production C. Pi custody and the permanent
job ledger are real SQLite stores. No board, network or simulated result packet
is used; delayed packets are bytes actually emitted by that C endpoint.
"""
from contextlib import contextmanager
from dataclasses import asdict
import json
import time
from types import SimpleNamespace
import uuid

import pytest

from job_safety import PermanentJobSafety
from native_business_runtime import NativeBusinessRuntime
from hardware.tests.test_mcu_simplified_execution import library, runtime, select
from hardware.tests.test_mcu_work_preparation import original_scope
from hardware.tests.test_native_business_runtime import CSerial, retained_context, start_command
from hardware.tests.test_native_business_completion import put_baseline
from hardware.tests.test_native_delivery_issue_confirmation import issue_confirmation_wire
from hardware.tests.test_native_runtime_rpc import DelayedUpdaterClient, poll_until
from hardware.tests.test_native_result_confirmation import confirmation_wire
from hardware.tests.test_simplified_mcu_pi_business import real_work, finish_delivery_round
import uart2_protocol as uart


def preserve_actual_first_weight(case):
    # Optional historical/diagnostic custody is not a new normal execution
    # dependency. Obtain the existing real C mailbox; do not fabricate a weight.
    query = original_scope(case.start) | dict(eventMessageType="WORK_PREOPEN_WEIGHT_READY",
        stepSequence=1, configVersion=case.start["configVersion"])
    responses = case.wire.exchange("QUERY_PROCESS_EVENT", query)
    raw = next(reply["payload"] for reply in responses if reply["messageName"] == "WORK_PREOPEN_WEIGHT_READY")
    scope = uart.encode_payload("QUERY_PROCESS_EVENT", query)[8:]
    case.store.save_native_process_receipt(scope, "WORK_PREOPEN_WEIGHT_READY", raw)
    assert uart.decode_payload("WORK_PREOPEN_WEIGHT_READY", raw)["reportedWeightGrams"] == 500
    return raw


@contextmanager
def running_case(runtime, tmp_path, *, preserve_weight=False):
    with real_work(runtime, tmp_path, False) as case:
        case.clean = False
        retained_context(case)
        case.initial_raw = preserve_actual_first_weight(case) if preserve_weight else None
        case.clock = SimpleNamespace(now=0)
        case.client = DelayedUpdaterClient(case.safety._client.store)
        case.safety = PermanentJobSafety(case.client)
        owner = NativeBusinessRuntime(case.store, case.safety, device_name="device-1",
            connected=lambda: True, clock=lambda: case.clock.now)
        case.serial = CSerial(case.wire)
        owner.open(port="host-test-no-device", port_factory=lambda **options: case.serial)
        case.owners = [owner]
        try:
            poll_until(case, owner, lambda: owner.uart_state == "READY")
            yield case, owner
        finally:
            case.client.release.set()
            for active in reversed(case.owners):
                close_owner(active)


def close_owner(owner):
    owner.close()
    owner._rpc._thread.join(timeout=2)
    assert not owner._rpc._thread.is_alive()


def restart_pi(case, owner):
    close_owner(owner)
    case.store.close()
    case.store.initialize()
    restarted = NativeBusinessRuntime(case.store, case.safety, device_name="device-1",
        connected=lambda: True, clock=lambda: case.clock.now)
    case.serial = CSerial(case.wire)
    restarted.open(port="host-test-no-device", port_factory=lambda **options: case.serial)
    case.owners.append(restarted)
    return restarted


def reboot_actual_mcu(runtime, case, owner):
    old_boot = owner.boot.current_boot(case.clock.now)
    assert old_boot == case.boot
    lib, endpoint, preparation, replies, _, sink, guard = runtime
    lib.TestFacts_InitHardware()
    lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
    assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
    assert lib.ActuatorRuntime_SetDoorTarget(1)
    case.wire.now = 0
    replies.clear()
    case.serial.rx.clear()
    poll_until(case, owner, lambda: owner.boot.current_boot(case.clock.now) not in {None, old_boot})
    return owner.boot.current_boot(case.clock.now)


def actual_complete_frame(runtime, case):
    now = finish_delivery_round(runtime, case, case.wire.now, 2, 700)
    assert select(runtime, case.delivery, now, "END")
    case.wire.now = now
    case.wire.take()  # Earlier in-flight replies are deliberately lost at transport.
    reply = case.wire.exchange("QUERY_WORK", original_scope(case.start))[0]
    work = uart.decode_payload(reply["messageName"], reply["payload"])
    assert work["status"] == "RESULT_HELD"
    query_id = case.store.reserve_native_query_id()
    query = dict(queryId=query_id, mcuBootId=case.boot, resultSequence=work["resultSequence"],
        resultDigestSha256=work["resultDigestSha256"], workUid=case.permit.work_uid)
    case.wire.write(uart.encode_frame("QUERY_RESULT", query_id, uart.encode_payload("QUERY_RESULT", query)))
    frame = next(frame for frame in case.wire.take() if uart.decode_frame(frame)["messageName"] == "WORK_RESULT")
    result = uart.decode_payload("WORK_RESULT", uart.decode_frame(frame)["payload"])
    assert (result["initialWeightGrams"], result["finalWeightGrams"], result["finishReason"]) == (500, 700, "DELIVERY_END")
    return frame


def archived_by_poll(runtime, case, owner):
    reboot_actual_mcu(runtime, case, owner)
    poll_until(case, owner, lambda: case.store.get_native_delivery_issue(case.permit.work_uid) is not None)
    issue = case.store.get_native_delivery_issue(case.permit.work_uid)
    poll_until(case, owner, lambda: case.store.get_event(issue["issueUid"]) is not None)
    return issue


def confirm_issue(case, issue, *, event_uid=None, quarantined=False):
    _, _, confirmation = issue_confirmation_wire(case, event_uid or issue["issueUid"], quarantined=quarantined)
    assert case.store.receive_business_confirmation_and_create_receipt(command=confirmation, device_name="device-1") == "ACCEPTED"
    return confirmation


def final_evidence_reports(case):
    return [row for row in case.store.list_native_delivery_issue_reports(case.permit.work_uid)
        if row["evidence_kind"] == "FINAL_RESULT"]


def retain_later_work_without_dispatch(case, owner):
    # Establish only the next local occupancy boundary. This does not claim
    # MCU configuration/admission has recovered or send its prepared START.
    command = start_command()
    assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
    assert case.store.claim_next_command()["command_uid"] == command["commandUid"]
    permit = case.safety.request_job(command, work_type="DELIVERY", work_uid=command["payload"]["sessionUid"])
    case.safety.begin_job(permit, begin_uid=permit.work_uid, digest=permit.request_digest_sha256)
    values = {key: value for key, value in case.start.items()
        if key not in {"mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence"}}
    values["sessionUid"] = permit.work_uid
    record = case.store.prepare_native_command("START_DELIVERY_SESSION", str(uuid.uuid4()),
        owner.boot.current_boot(case.clock.now), values)
    context = dict(native_protocol=2, phase="NATIVE_RUNNING", start_command_uid=permit.command_uid,
        start_mcu_command_uid=record["command_uid"], start_runtime_instance_uid=owner._runtime_instance_uid,
        job_safety=asdict(permit) | {"begin_uid": permit.work_uid})
    assert case.store.acquire_work_slot("DELIVERY", permit.work_uid, 1, context)
    baseline = put_baseline(case.store, command["payload"]["bagUid"], source_work_uid=permit.work_uid, grams=83)
    return case.store.get_work_slot(), baseline


def test_real_mcu_restart_automatically_archives_original_delivery_and_existing_weight(runtime, tmp_path):
    with running_case(runtime, tmp_path, preserve_weight=True) as (case, owner):
        original_context = case.store.get_work_slot()["context"]
        new_boot = reboot_actual_mcu(runtime, case, owner)
        poll_until(case, owner, lambda: case.store.get_native_delivery_issue(case.permit.work_uid) is not None)
        issue = case.store.get_native_delivery_issue(case.permit.work_uid)
        assert issue["reason"] == "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
        assert issue["reasonText"] == "单片机重启，未取得最终结果包"
        assert issue["settlementAllowed"] is False
        assert issue["sourceMcuBootId"] == case.boot and issue["targetMcuBootId"] == new_boot
        assert issue["originalWorkContext"] == original_context
        first = next(row for row in case.store.list_native_delivery_issue_facts(issue["issueUid"])
            if row["message_name"] == "WORK_PREOPEN_WEIGHT_READY")
        assert first["payload"] == case.initial_raw
        poll_until(case, owner, lambda: case.store.get_event(issue["issueUid"]) is not None)
        event = json.loads(case.store.get_event(issue["issueUid"])["payload_json"])
        assert event["eventType"] == "DELIVERY_ISSUE_ARCHIVED"
        assert event["payload"]["businessValue"] == "NONE"
        assert case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1") is None
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        assert case.client.calls["COMPLETE_JOB"] == 0


def test_complete_real_packet_saved_before_reboot_is_reported_normally_not_archived(runtime, tmp_path):
    with running_case(runtime, tmp_path) as (case, owner):
        frame = actual_complete_frame(runtime, case)
        raw = uart.decode_frame(frame)["payload"]
        case.store.save_native_mcu_result(raw)
        reboot_actual_mcu(runtime, case, owner)
        poll_until(case, owner, lambda: case.store.get_native_result_report(case.permit,
            case.start["mcuCommandUid"], device_name="device-1") is not None)
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None
        report = case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        assert event["eventType"] == "DELIVERY_COMPLETE"
        assert event["payload"]["deliveryNetWeightGrams"] == 200
        _, _, confirmation = confirmation_wire(case, report["eventUid"])
        assert case.store.receive_business_confirmation_and_create_receipt(command=confirmation, device_name="device-1") == "ACCEPTED"
        poll_until(case, owner, lambda: case.store.get_work_slot() is None)
        snapshot = case.safety.get_job_permit(case.permit.permit_uid)
        assert snapshot["state"] == "COMPLETED" and snapshot["completionOutcome"] == "SUCCEEDED"
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None


def test_final_packet_committed_after_reboot_evaluation_but_before_archive_transaction_wins(runtime, tmp_path, monkeypatch):
    with running_case(runtime, tmp_path) as (case, owner):
        frame = actual_complete_frame(runtime, case)
        raw = uart.decode_frame(frame)["payload"]
        original_archive = case.store.archive_native_delivery_issue
        crossings = []
        def receive_just_before_archive(permit, start_uid, **options):
            # The runtime already chose RECOVERY_INTENT_RECORDED. A real final
            # packet commits at this last boundary; the archive's own write
            # transaction must recheck custody instead of keeping that decision.
            assert permit == case.permit and start_uid == case.start["mcuCommandUid"]
            crossings.append(options["current_boot"]())
            case.store.save_native_mcu_result(raw)
            return original_archive(permit, start_uid, **options)
        monkeypatch.setattr(case.store, "archive_native_delivery_issue", receive_just_before_archive)
        new_boot = reboot_actual_mcu(runtime, case, owner)
        poll_until(case, owner, lambda: case.store.get_native_result_report(case.permit,
            case.start["mcuCommandUid"], device_name="device-1") is not None)
        assert crossings == [new_boot]
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None
        report = case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1")
        event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
        assert event["eventType"] == "DELIVERY_COMPLETE"
        assert event["payload"]["deliveryNetWeightGrams"] == 200


def test_communication_timeout_without_new_boot_never_fabricates_a_reboot_issue(runtime, tmp_path):
    with running_case(runtime, tmp_path) as (case, owner):
        case.serial.drop = lambda reply: True
        for _ in range(111):
            owner.poll()
            case.clock.now += 100
            time.sleep(0.001)
        assert owner.boot.current_boot(case.clock.now) is None
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None
        assert case.store.list_native_delivery_issue_reports(case.permit.work_uid) == []
        assert case.store.get_state("native_blocking_fault") == "MCU_COMMUNICATION_UNAVAILABLE"
        poll_until(case, owner, lambda: case.store.get_work_slot() is None)
        command = case.store.get_command(case.permit.command_uid)
        marker = command["result"]["nativeControlFailure"]
        assert command["state"] == "FAILED" and marker["state"] == "APPLIED"
        assert marker["evidence"]["reason"] == "MCU_COMMUNICATION_UNAVAILABLE"
        assert case.client.calls["COMPLETE_JOB"] == 1
        permanent = case.safety.get_job_permit(case.permit.permit_uid)
        assert permanent["state"] == "COMPLETED" and permanent["completionOutcome"] == "FAILED"


def test_pi_only_restart_retains_original_running_work_without_issue_or_start_replay(runtime, tmp_path):
    with running_case(runtime, tmp_path) as (case, owner):
        before = len(case.wire.sent)
        restarted = restart_pi(case, owner)
        poll_until(case, restarted, lambda: restarted.boot.current_boot(case.clock.now) == case.boot)
        for _ in range(30):
            restarted.poll()
            case.clock.now += 100
            time.sleep(0.001)
        assert case.store.get_native_delivery_issue(case.permit.work_uid) is None
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert "START_DELIVERY_SESSION" not in [frame["messageName"] for frame in case.wire.sent[before:]]


@pytest.mark.parametrize("receipt", ["platform", "other_evidence", "quarantined"])
def test_issue_needs_its_exact_archive_business_confirmation_before_cancelling(runtime, tmp_path, receipt):
    with running_case(runtime, tmp_path) as (case, owner):
        issue = archived_by_poll(runtime, case, owner)
        if receipt == "platform":
            event = case.store.get_event(issue["issueUid"])
            case.store.record_event_platform_reply(event["edge_event_sequence"], 200)
        elif receipt == "other_evidence":
            poll_until(case, owner, lambda: any(row["evidence_kind"] == "ARCHIVE_CONTEXT"
                for row in case.store.list_native_delivery_issue_reports(case.permit.work_uid)))
            part = next(row for row in case.store.list_native_delivery_issue_reports(case.permit.work_uid)
                if row["evidence_kind"] == "ARCHIVE_CONTEXT")
            confirm_issue(case, issue, event_uid=part["event_uid"])
        else:
            confirm_issue(case, issue, quarantined=True)
        for _ in range(40):
            owner.poll()
            case.clock.now += 100
            time.sleep(0.001)
        assert case.client.calls["COMPLETE_JOB"] == 0
        assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        assert case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1") is None


@pytest.mark.parametrize("committed_before_reply", [False, True])
def test_issue_waits_for_actual_cancelled_receipt_before_releasing_slot_and_never_changes_bag(
        runtime, tmp_path, committed_before_reply):
    with running_case(runtime, tmp_path) as (case, owner):
        bag_uid = case.store.get_command(case.permit.command_uid)["payload"]["payload"]["bagUid"]
        baseline = put_baseline(case.store, bag_uid, grams=79)
        issue = archived_by_poll(runtime, case, owner)
        case.client.delay("COMPLETE_JOB", after_commit=committed_before_reply)
        confirm_issue(case, issue)
        poll_until(case, owner, case.client.entered.is_set)
        assert not case.client.returned.is_set()
        confirmation = case.store.get_native_delivery_issue_confirmation(case.permit.work_uid, device_name="device-1")
        assert confirmation["outcome"] == "BUSINESS_APPLIED" and confirmation["resultReferences"] == []
        for _ in range(111):
            owner.poll()
            assert case.store.get_work_slot()["work_uid"] == case.permit.work_uid
            assert case.store.get_bag_baseline(bag_uid) == baseline
            case.clock.now += 100
            time.sleep(0.001)
        snapshot = case.client.store.get_job_permit({"permitUid": case.permit.permit_uid})
        assert snapshot["state"] == ("COMPLETED" if committed_before_reply else "ACTIVE")
        assert not case.client.watchdog_fired.is_set()
        assert not case.store.get_state("native_blocking_fault")
        case.client.release.set()
        poll_until(case, owner, lambda: case.store.get_work_slot() is None)
        snapshot = case.safety.get_job_permit(case.permit.permit_uid)
        assert snapshot["state"] == "COMPLETED" and snapshot["completionOutcome"] == "CANCELLED"
        command = case.store.get_command(case.permit.command_uid)
        assert command["state"] == "FAILED" and command["last_error"] == "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
        assert command["result"]["nativeIssueCompletion"]["state"] == "APPLIED"
        assert case.store.get_bag_baseline(bag_uid) == baseline
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
        assert case.client.calls["COMPLETE_JOB"] == 1


@pytest.mark.parametrize("crash_after_save", [False, True])
def test_late_actual_result_after_issue_slot_release_is_only_evidence_even_across_pi_crash(
        runtime, tmp_path, monkeypatch, crash_after_save):
    with running_case(runtime, tmp_path) as (case, owner):
        frame = actual_complete_frame(runtime, case)
        raw = uart.decode_frame(frame)["payload"]
        issue = archived_by_poll(runtime, case, owner)
        confirm_issue(case, issue)
        poll_until(case, owner, lambda: case.store.get_work_slot() is None)
        owner.poll()  # The previous work-query owner no longer has a slot.
        assert not final_evidence_reports(case)
        case.serial.rx.extend(frame)
        if crash_after_save:
            original_save = case.store.save_native_mcu_result
            def save_then_crash(payload):
                receipt = original_save(payload)
                assert payload == raw
                raise RuntimeError("injected Pi crash after durable late-result save")
            monkeypatch.setattr(case.store, "save_native_mcu_result", save_then_crash)
            with pytest.raises(RuntimeError, match="after durable late-result"):
                owner.poll()
            assert not final_evidence_reports(case)
            monkeypatch.setattr(case.store, "save_native_mcu_result", original_save)
            owner = restart_pi(case, owner)
        poll_until(case, owner, lambda: bool(final_evidence_reports(case)))
        rows = final_evidence_reports(case)
        assert len(rows) == 1
        event = json.loads(case.store.get_event(rows[0]["event_uid"])["payload_json"])
        assert event["eventType"] == "DELIVERY_ISSUE_EVIDENCE_APPENDED"
        assert event["payload"]["issueUid"] == issue["issueUid"]
        assert event["payload"]["businessValue"] == "NONE"
        assert bytes.fromhex(event["payload"]["dataHex"]) == raw
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
        assert case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1") is None
        assert case.store.get_work_slot() is None
        assert case.safety.get_job_permit(case.permit.permit_uid)["completionOutcome"] == "CANCELLED"
        for _ in range(5):
            case.serial.rx.extend(frame)
            owner.poll()
        assert final_evidence_reports(case) == rows
        assert case.client.calls["COMPLETE_JOB"] == 1


def test_old_issue_confirmation_and_late_result_never_touch_later_prepared_slot_or_bag(runtime, tmp_path):
    with running_case(runtime, tmp_path) as (case, owner):
        frame = actual_complete_frame(runtime, case)
        issue = archived_by_poll(runtime, case, owner)
        confirmation = confirm_issue(case, issue)
        poll_until(case, owner, lambda: case.store.get_work_slot() is None)
        later_slot, baseline = retain_later_work_without_dispatch(case, owner)
        assert case.store.receive_business_confirmation_and_create_receipt(command=confirmation, device_name="device-1") == "DUPLICATE"
        case.serial.rx.extend(frame)
        poll_until(case, owner, lambda: bool(final_evidence_reports(case)))
        for _ in range(30):
            owner.poll()
            case.clock.now += 10
            time.sleep(0.001)
        assert case.store.get_work_slot() == later_slot
        assert case.store.get_bag_baseline(baseline["bag_uid"]) == baseline
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == issue
        assert case.client.calls["COMPLETE_JOB"] == 1
        assert case.store.get_command(case.permit.command_uid)["state"] == "FAILED"
        assert case.store.get_native_result_report(case.permit, case.start["mcuCommandUid"], device_name="device-1") is None
        assert [frame["messageName"] for frame in case.wire.sent].count("START_DELIVERY_SESSION") == 1
