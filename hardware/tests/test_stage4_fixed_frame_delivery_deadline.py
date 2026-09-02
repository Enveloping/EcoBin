"""Durable monotonic overdue regressions for fixed-frame delivery."""

import copy
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from command_processor import CommandProcessor
from job_safety import JobSafetyError, PermanentJobSafety
from local_control import LocalControlUnavailable
from onenet_wire import canonical_payload_sha256
from tests.test_stage4_fixed_frame_convergence import (
    _compat_result,
    _runtime,
    valid_compat_service_command,
)
from tests.test_stage4_fixed_frame_receipt_recovery import (
    FaultInjectingUpdaterClient,
)
from work_manager import WorkManager


def _start_delivery_waiting_for_dd(
    runtime,
    wall_reference,
    *,
    authorization_seconds=30,
):
    command = valid_compat_service_command(
        "start-delivery-session.service-wire.json"
    )
    command["issuedAt"] = wall_reference.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    command["expiresAt"] = (
        wall_reference + timedelta(seconds=authorization_seconds)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert runtime.edge.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"

    assert runtime.processor.process_next() is True
    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "WAITING_MCU_RESULT"
    )
    assert runtime.edge.get_work_slot() is not None
    assert len(runtime.uart.calls) == 1
    return command


def _assert_delivery_overdue_is_recovery_locked(
    runtime,
    command,
    uart_calls,
):
    inbox = runtime.edge.get_command(command["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "MCU_RESULT_OVERDUE"
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    assert retained["work_state"] == "RECOVERY_REQUIRED"
    assert retained["context"]["phase"] == (
        "FIXED_FRAME_RESULT_OVERDUE_RECOVERY_REQUIRED"
    )
    assert retained["context"]["delivery_result_overdue"] is True
    assert "pending_completion" not in retained["context"]["job_safety"]
    assert runtime.uart.calls == uart_calls
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "ACTIVE"
    assert runtime.updater.get_status()["jobGateState"] == "LOCKED"


def _start_delivery_with_post_arm_uart_error(runtime, wall_reference):
    command = valid_compat_service_command(
        "start-delivery-session.service-wire.json"
    )
    command["issuedAt"] = wall_reference.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    command["expiresAt"] = (
        wall_reference + timedelta(seconds=30)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def fail_after_arm(
        message_name,
        values,
        *,
        mcu_command_uid,
        dispatch_deadline_monotonic,
        dispatch_gate,
    ):
        assert dispatch_deadline_monotonic > 0
        dispatch_gate()
        # Model a full frame accepted by the serial driver followed by a
        # flush failure.  The MCU may therefore execute this exact AA.
        runtime.uart.calls.append(
            (message_name, dict(values), mcu_command_uid)
        )
        raise OSError("simulated UART flush failure after full write")

    runtime.uart.send_command_before_deadline = fail_after_arm
    assert runtime.edge.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    assert runtime.processor.process_next() is True
    return command


def _start_clean_with_post_arm_uart_error(runtime):
    command = valid_compat_service_command(
        "start-clean-operation.service-wire.json"
    )
    command["payload"]["oldBaselineWeightGrams"] = 1_500
    command["payloadSha256"] = canonical_payload_sha256(
        command["payload"]
    )

    def fail_after_arm(
        message_name,
        values,
        *,
        mcu_command_uid,
        dispatch_deadline_monotonic,
        dispatch_gate,
    ):
        assert dispatch_deadline_monotonic > 0
        dispatch_gate()
        runtime.uart.calls.append(
            (message_name, dict(values), mcu_command_uid)
        )
        raise OSError("simulated clean UART flush failure")

    runtime.uart.send_command_before_deadline = fail_after_arm
    assert runtime.edge.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    assert runtime.processor.process_next() is True
    return command


def test_fixed_frame_delivery_uses_frozen_monotonic_deadline_when_wall_clock_lost(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    wall_clock = {"reference": wall_reference}
    ticks = {"seconds": 100.0}
    boot = {"identity": "linux:delivery-deadline-boot-a"}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_clock["reference"],
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_clock["reference"],
    )
    monkeypatch.setattr(
        "work_manager.time.monotonic",
        lambda: ticks["seconds"],
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: int(ticks["seconds"] * 1_000),
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: boot["identity"],
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_waiting_for_dd(runtime, wall_reference)
    uart_calls = list(runtime.uart.calls)
    frozen = runtime.edge.get_work_slot()["context"]
    assert frozen["delivery_result_window_ms"] == 150_000
    assert frozen["delivery_result_deadline_monotonic_ms"] == 250_000

    # Recreate the business coordinator to prove the deadline came from the
    # durable work context, not an in-memory timer. NTP/trusted UTC becomes
    # unavailable only after the original command was accepted and sent.
    wall_clock["reference"] = None
    restarted_work = WorkManager(
        runtime.edge,
        runtime.uart,
        None,
        runtime.photos,
        job_safety=runtime.safety,
    )
    ticks["seconds"] = 249.999
    assert restarted_work.expire_fixed_frame_work() is False
    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "WAITING_MCU_RESULT"
    )

    ticks["seconds"] = 250.001
    assert restarted_work.expire_fixed_frame_work() is True
    _assert_delivery_overdue_is_recovery_locked(
        runtime,
        command,
        uart_calls,
    )
    runtime.updater.close()
    runtime.edge.close()


def test_fixed_frame_dd_after_cloud_authorization_but_inside_result_window_completes(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    wall_clock = {"reference": wall_reference}
    ticks = {"seconds": 100.0}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_clock["reference"],
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_clock["reference"],
    )
    monkeypatch.setattr(
        "work_manager.time.monotonic",
        lambda: ticks["seconds"],
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: int(ticks["seconds"] * 1_000),
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:delivery-result-window-boot",
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_waiting_for_dd(
        runtime,
        wall_reference,
        authorization_seconds=60,
    )

    # The cloud command's 60-second first-dispatch authorization has elapsed,
    # but the MCU's 120-second auto-close plus 30-second result allowance has
    # not.  The final DD is still the authoritative business terminal fact.
    wall_clock["reference"] = wall_reference + timedelta(seconds=61)
    ticks["seconds"] = 161.0
    assert runtime.work.expire_fixed_frame_work() is False

    runtime.processor.process_mcu_event(_compat_result(command))

    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "COMPLETED"
    )
    assert runtime.edge.get_work_slot() is None
    assert any(
        row["event_type"] == "DELIVERY_COMPLETE"
        for row in runtime.edge.list_pending_events(limit=100)
    )
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "COMPLETED"
    assert runtime.updater.get_status()["jobGateState"] == "OPEN"
    runtime.updater.close()
    runtime.edge.close()


def test_fixed_frame_delivery_boot_identity_change_fails_closed_without_extension(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    wall_clock = {"reference": wall_reference}
    ticks = {"seconds": 100.0}
    boot = {"identity": "linux:delivery-deadline-boot-a"}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_clock["reference"],
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_clock["reference"],
    )
    monkeypatch.setattr(
        "work_manager.time.monotonic",
        lambda: ticks["seconds"],
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: int(ticks["seconds"] * 1_000),
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: boot["identity"],
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_waiting_for_dd(runtime, wall_reference)
    uart_calls = list(runtime.uart.calls)

    # The numeric monotonic value is still before the old deadline, but it
    # belongs to another boot and therefore cannot safely extend the wait.
    wall_clock["reference"] = None
    boot["identity"] = "linux:delivery-deadline-boot-b"
    ticks["seconds"] = 101.0
    restarted_work = WorkManager(
        runtime.edge,
        runtime.uart,
        None,
        runtime.photos,
        job_safety=runtime.safety,
    )

    assert restarted_work.expire_fixed_frame_work() is True
    _assert_delivery_overdue_is_recovery_locked(
        runtime,
        command,
        uart_calls,
    )
    runtime.updater.close()
    runtime.edge.close()


def test_same_process_late_dd_completes_overdue_delivery_normally(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    ticks = {"seconds": 100.0}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: int(ticks["seconds"] * 1_000),
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:same-process-late-dd",
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_waiting_for_dd(runtime, wall_reference)
    uart_calls = list(runtime.uart.calls)

    ticks["seconds"] = 250.001
    assert runtime.work.expire_fixed_frame_work() is True
    _assert_delivery_overdue_is_recovery_locked(
        runtime,
        command,
        uart_calls,
    )

    runtime.processor.process_mcu_event(_compat_result(command))

    completed = runtime.edge.get_command(command["commandUid"])
    assert completed["state"] == "COMPLETED"
    assert completed["result"]["resultOverdue"] is True
    assert runtime.edge.get_work_slot() is None
    events = [
        row
        for row in runtime.edge.list_pending_events(limit=100)
        if row["event_type"] == "DELIVERY_COMPLETE"
    ]
    assert len(events) == 1
    event_payload = json.loads(events[0]["payload_json"])["payload"]
    assert event_payload["completionReason"] == "USER_ENDED"
    assert event_payload["manualReviewRequired"] is False
    observation = json.loads(
        runtime.edge.get_state("fixed_frame_latest_observation_json")
    )
    assert observation["resultOverdue"] is True
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "COMPLETED"
    assert permit["completionOutcome"] == "SUCCEEDED"
    assert runtime.updater.get_status()["jobGateState"] == "OPEN"
    runtime.updater.close()
    runtime.edge.close()


def test_cross_process_late_dd_without_live_token_stays_recovery_locked(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    ticks = {"seconds": 100.0}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: int(ticks["seconds"] * 1_000),
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:lost-live-token",
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_waiting_for_dd(runtime, wall_reference)
    ticks["seconds"] = 250.001
    assert runtime.work.expire_fixed_frame_work() is True

    restarted_work = WorkManager(
        runtime.edge,
        runtime.uart,
        None,
        runtime.photos,
        job_safety=runtime.safety,
    )
    with pytest.raises(
        JobSafetyError,
        match="live dispatch token was lost",
    ):
        restarted_work.handle_mcu_event(_compat_result(command))

    inbox = runtime.edge.get_command(command["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "MCU_RESULT_OVERDUE"
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    assert retained["work_state"] == "RECOVERY_REQUIRED"
    assert retained["context"]["phase"] == (
        "FIXED_FRAME_RESULT_OVERDUE_RECOVERY_REQUIRED"
    )
    assert [
        row
        for row in runtime.edge.list_pending_events(limit=100)
        if row["event_type"] == "DELIVERY_COMPLETE"
    ] == []
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "ACTIVE"
    assert runtime.updater.get_status()["jobGateState"] == "LOCKED"
    runtime.updater.close()
    runtime.edge.close()


def test_queued_dd_still_converges_when_overdue_check_runs_first(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    ticks = {"seconds": 100.0}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: int(ticks["seconds"] * 1_000),
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:queued-dd-overdue-race",
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_waiting_for_dd(runtime, wall_reference)
    result = _compat_result(command)
    stored_frame = {
        "message_name": result["message_name"],
        "message_type": result["message_type"],
        "tx_sequence": result["source_tx_sequence"],
        "payload": result["payload"],
    }
    assert runtime.edge.receive_mcu_frame(stored_frame) == "ACCEPTED"

    # main.py checks elapsed work before draining mcu_event_inbox.  The
    # queued, already-received DD must still finish A in that ordering.
    ticks["seconds"] = 250.001
    assert runtime.work.expire_fixed_frame_work() is True
    pending = runtime.edge.list_pending_mcu_events()
    assert len(pending) == 1
    runtime.processor.process_mcu_event(pending[0])
    assert runtime.edge.mark_mcu_event_processed(
        pending[0]["mcu_boot_id"],
        pending[0]["mcu_event_sequence"],
        pending[0]["mcu_receive_generation"],
    )

    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "COMPLETED"
    )
    assert runtime.edge.get_work_slot() is None
    assert runtime.edge.list_pending_mcu_events() == []
    runtime.updater.close()
    runtime.edge.close()


def test_missing_start_row_with_disabled_adapter_retains_protected_slot(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    ticks = {"seconds": 100.0}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: int(ticks["seconds"] * 1_000),
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:missing-start-row",
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_waiting_for_dd(runtime, wall_reference)
    original_uart_calls = list(runtime.uart.calls)
    with runtime.edge.transaction():
        runtime.edge._conn.execute(
            "DELETE FROM command_inbox WHERE command_uid=?",
            (command["commandUid"],),
        )

    # Simulate a mode-mismatched process: the retained context says the
    # operation is protected, but this WorkManager has DisabledJobSafety.
    # A missing row is still state damage and can never authorize slot release.
    disabled_work = WorkManager(
        runtime.edge,
        runtime.uart,
        None,
        runtime.photos,
    )
    ticks["seconds"] = 250.001
    assert disabled_work.expire_fixed_frame_work() is True
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    assert retained["work_uid"] == command["payload"]["sessionUid"]
    assert retained["work_state"] == "RECOVERY_REQUIRED"
    assert retained["context"]["phase"] == (
        "FIXED_FRAME_STATE_CORRUPT_RECOVERY_REQUIRED"
    )
    assert retained["context"]["fixed_frame_recovery_error"] == (
        "START_COMMAND_NOT_FOUND"
    )
    assert disabled_work.expire_fixed_frame_work() is False

    second = copy.deepcopy(command)
    second["commandUid"] = str(uuid.uuid4())
    second["payload"]["sessionUid"] = str(uuid.uuid4())
    second["target"]["uid"] = second["payload"]["sessionUid"]
    second["payloadSha256"] = canonical_payload_sha256(second["payload"])
    assert runtime.edge.receive_command(
        second["commandUid"],
        second["commandType"],
        second,
    ) == "ACCEPTED"
    disabled_processor = CommandProcessor(
        runtime.edge,
        runtime.uart,
        disabled_work,
    )
    assert disabled_processor.process_next() is True
    assert runtime.edge.get_command(second["commandUid"])["last_error"] == (
        "DEVICE_BUSY"
    )
    assert runtime.uart.calls == original_uart_calls
    with pytest.raises(
        JobSafetyError,
        match="cannot be bound to its original command",
    ):
        disabled_work.handle_mcu_event(_compat_result(command))
    assert runtime.edge.get_work_slot() is not None
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "ACTIVE"
    assert runtime.updater.get_status()["jobGateState"] == "LOCKED"
    runtime.updater.close()
    runtime.edge.close()


@pytest.mark.parametrize("invalid_expires_at", [None, "not-a-time"])
def test_legacy_delivery_with_invalid_deadline_context_enters_corrupt_lock(
    tmp_path,
    monkeypatch,
    invalid_expires_at,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: 100_000,
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:invalid-legacy-context",
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_waiting_for_dd(runtime, wall_reference)
    slot = runtime.edge.get_work_slot()
    context = slot["context"]
    context.pop("delivery_result_deadline_monotonic_ms")
    context.pop("delivery_result_deadline_boot_identity")
    if invalid_expires_at is None:
        context.pop("expires_at")
    else:
        context["expires_at"] = invalid_expires_at
    runtime.edge.update_work_context(slot["work_uid"], context)

    assert runtime.work.expire_fixed_frame_work() is True
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    assert retained["work_state"] == "RECOVERY_REQUIRED"
    assert retained["context"]["phase"] == (
        "FIXED_FRAME_STATE_CORRUPT_RECOVERY_REQUIRED"
    )
    assert retained["context"]["fixed_frame_recovery_error"] == (
        "DELIVERY_DEADLINE_CONTEXT_INVALID"
    )
    inbox = runtime.edge.get_command(command["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "FIXED_FRAME_STATE_CORRUPT"
    assert runtime.work.expire_fixed_frame_work() is False
    runtime.updater.close()
    runtime.edge.close()


def test_wrong_start_command_session_binding_enters_corrupt_lock(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    ticks = {"milliseconds": 100_000}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:wrong-command-binding",
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_waiting_for_dd(runtime, wall_reference)
    row = runtime.edge.get_command(command["commandUid"])
    damaged = row["payload"]
    damaged["payload"]["sessionUid"] = str(uuid.uuid4())
    damaged["target"]["uid"] = damaged["payload"]["sessionUid"]
    with runtime.edge.transaction():
        runtime.edge._conn.execute(
            "UPDATE command_inbox SET payload_json=? WHERE command_uid=?",
            (
                json.dumps(damaged, ensure_ascii=False),
                command["commandUid"],
            ),
        )

    ticks["milliseconds"] = 250_001
    assert runtime.work.expire_fixed_frame_work() is True
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    assert retained["work_state"] == "RECOVERY_REQUIRED"
    assert retained["context"]["fixed_frame_recovery_error"] == (
        "START_COMMAND_BINDING_INVALID"
    )
    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "RECOVERY_REQUIRED"
    )
    runtime.updater.close()
    runtime.edge.close()


def test_post_arm_uart_error_reaches_overdue_then_late_dd_completes(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    ticks = {"milliseconds": 100_000}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:post-arm-uart-error",
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_with_post_arm_uart_error(
        runtime,
        wall_reference,
    )

    inbox = runtime.edge.get_command(command["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "UART_ACK_RESULT_UNKNOWN"
    assert inbox["result"]["physicalEffect"] == "UNKNOWN"
    assert inbox["result"]["error"] == "UART_WRITE_RESULT_UNKNOWN"
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    assert retained["context"]["phase"] == "START_RESULT_UNKNOWN"
    assert retained["context"][
        "delivery_result_deadline_monotonic_ms"
    ] == 250_000
    action = retained["context"]["job_safety"]["actions"][
        "DELIVERY:START:0"
    ]
    assert action["dispatch_result"] == "ARMED"
    permanent = runtime.updater.get_physical_action(
        {"actionUid": action["action_uid"]}
    )
    assert permanent["state"] == "ARMED"
    stages = {
        row["stage"]
        for row in runtime.edge._conn.execute(
            "SELECT stage FROM command_observation WHERE command_uid=?",
            (command["commandUid"],),
        ).fetchall()
    }
    assert "FAILED" not in stages

    ticks["milliseconds"] = 250_001
    assert runtime.work.expire_fixed_frame_work() is True
    _assert_delivery_overdue_is_recovery_locked(
        runtime,
        command,
        list(runtime.uart.calls),
    )

    runtime.processor.process_mcu_event(_compat_result(command))

    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "COMPLETED"
    )
    assert runtime.edge.get_work_slot() is None
    events = [
        row
        for row in runtime.edge.list_pending_events(limit=100)
        if row["event_type"] == "DELIVERY_COMPLETE"
    ]
    assert len(events) == 1
    permanent = runtime.updater.get_physical_action(
        {"actionUid": action["action_uid"]}
    )
    assert permanent["state"] == "CONFIRMED"
    assert permanent["confirmedOutcome"] == "EXECUTED"
    runtime.updater.close()
    runtime.edge.close()


def test_legacy_failed_starting_row_with_exact_live_arm_accepts_late_dd(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    ticks = {"milliseconds": 100_000}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:legacy-failed-starting",
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_with_post_arm_uart_error(
        runtime,
        wall_reference,
    )
    slot = runtime.edge.get_work_slot()
    assert slot is not None
    legacy_context = slot["context"]
    legacy_context["phase"] = "STARTING"
    runtime.edge.update_work_context(slot["work_uid"], legacy_context)
    with runtime.edge.transaction():
        runtime.edge._conn.execute(
            """UPDATE command_inbox
               SET state='FAILED', last_error='UART_WRITE_FAILED'
               WHERE command_uid=?""",
            (command["commandUid"],),
        )

    ticks["milliseconds"] = 250_001
    assert runtime.work.expire_fixed_frame_work() is True
    overdue = runtime.edge.get_work_slot()
    assert overdue is not None
    assert overdue["context"]["phase"] == (
        "FIXED_FRAME_RESULT_OVERDUE_RECOVERY_REQUIRED"
    )
    assert runtime.edge.get_command(command["commandUid"])["last_error"] == (
        "MCU_RESULT_OVERDUE"
    )

    runtime.processor.process_mcu_event(_compat_result(command))

    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "COMPLETED"
    )
    assert runtime.edge.get_work_slot() is None
    assert len(
        [
            row
            for row in runtime.edge.list_pending_events(limit=100)
            if row["event_type"] == "DELIVERY_COMPLETE"
        ]
    ) == 1
    runtime.updater.close()
    runtime.edge.close()


def test_post_arm_uart_error_restart_loses_token_and_stays_locked(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    ticks = {"milliseconds": 100_000}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:post-arm-restart",
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_with_post_arm_uart_error(
        runtime,
        wall_reference,
    )
    restarted_work = WorkManager(
        runtime.edge,
        runtime.uart,
        None,
        runtime.photos,
        job_safety=runtime.safety,
    )
    restarted_processor = CommandProcessor(
        runtime.edge,
        runtime.uart,
        restarted_work,
    )

    ticks["milliseconds"] = 250_001
    assert restarted_work.expire_fixed_frame_work() is True
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    assert retained["work_state"] == "RECOVERY_REQUIRED"
    assert retained["context"]["phase"] == (
        "FIXED_FRAME_STATE_CORRUPT_RECOVERY_REQUIRED"
    )
    assert retained["context"]["fixed_frame_recovery_error"] == (
        "START_ACTION_LIVE_PROOF_INVALID"
    )
    assert runtime.edge.get_command(command["commandUid"])["last_error"] == (
        "FIXED_FRAME_STATE_CORRUPT"
    )

    with pytest.raises(
        JobSafetyError,
        match="cannot be bound to its original command",
    ):
        restarted_processor.process_mcu_event(_compat_result(command))
    assert runtime.edge.get_work_slot() is not None
    assert [
        row
        for row in runtime.edge.list_pending_events(limit=100)
        if row["event_type"] == "DELIVERY_COMPLETE"
    ] == []
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "ACTIVE"
    assert runtime.updater.get_status()["jobGateState"] == "LOCKED"
    runtime.updater.close()
    runtime.edge.close()


def test_post_arm_overdue_waits_when_permanent_action_is_unreadable(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    ticks = {"milliseconds": 100_000}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:post-arm-gate-unavailable",
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_with_post_arm_uart_error(
        runtime,
        wall_reference,
    )
    original_client = runtime.safety._client

    class UnreadablePhysicalActionClient:
        def request(self, action, payload):
            if action == "GET_PHYSICAL_ACTION":
                raise LocalControlUnavailable(
                    "simulated updater read outage"
                )
            return original_client.request(action, payload)

    runtime.safety._client = UnreadablePhysicalActionClient()
    ticks["milliseconds"] = 250_001

    assert runtime.work.expire_fixed_frame_work() is False
    inbox = runtime.edge.get_command(command["commandUid"])
    retained = runtime.edge.get_work_slot()
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "UART_ACK_RESULT_UNKNOWN"
    assert retained is not None
    assert retained["work_state"] == "ACTIVE"
    assert retained["context"]["phase"] == "START_RESULT_UNKNOWN"
    assert "fixed_frame_recovery_error" not in retained["context"]
    runtime.updater.close()
    runtime.edge.close()


def test_post_arm_overdue_rejects_mismatched_permanent_action_identity(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    ticks = {"milliseconds": 100_000}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:post-arm-identity-mismatch",
    )
    runtime = _runtime(tmp_path)
    command = _start_delivery_with_post_arm_uart_error(
        runtime,
        wall_reference,
    )
    real_get_action = runtime.safety.get_physical_action

    def mismatched_get_action(action_uid):
        result = real_get_action(action_uid)
        result["actionDigestSha256"] = "0" * 64
        return result

    monkeypatch.setattr(
        runtime.safety,
        "get_physical_action",
        mismatched_get_action,
    )
    ticks["milliseconds"] = 250_001

    assert runtime.work.expire_fixed_frame_work() is True
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    assert retained["context"]["phase"] == (
        "FIXED_FRAME_STATE_CORRUPT_RECOVERY_REQUIRED"
    )
    assert retained["context"]["fixed_frame_recovery_error"] == (
        "START_ACTION_LIVE_PROOF_INVALID"
    )
    assert runtime.edge.get_command(command["commandUid"])["last_error"] == (
        "FIXED_FRAME_STATE_CORRUPT"
    )
    assert runtime.updater.get_status()["jobGateState"] == "LOCKED"
    runtime.updater.close()
    runtime.edge.close()


def test_clean_post_arm_uart_error_expires_then_accepts_live_ef(
    tmp_path,
    monkeypatch,
):
    ticks = {"milliseconds": 10_000}
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:clean-post-arm-error",
    )
    runtime = _runtime(tmp_path)
    command = _start_clean_with_post_arm_uart_error(runtime)

    inbox = runtime.edge.get_command(command["commandUid"])
    retained = runtime.edge.get_work_slot()
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "UART_ACK_RESULT_UNKNOWN"
    assert retained is not None
    assert retained["context"]["phase"] == "START_RESULT_UNKNOWN"
    stages = {
        row["stage"]
        for row in runtime.edge._conn.execute(
            "SELECT stage FROM command_observation WHERE command_uid=?",
            (command["commandUid"],),
        ).fetchall()
    }
    assert "FAILED" not in stages

    ticks["milliseconds"] += (
        retained["context"]["operation_window_ms"] + 1
    )
    assert runtime.work.expire_fixed_frame_work() is True
    overdue = runtime.edge.get_work_slot()
    assert overdue is not None
    assert overdue["context"]["phase"] == (
        "FIXED_FRAME_RESULT_OVERDUE_RECOVERY_REQUIRED"
    )
    assert overdue["context"]["clean_result_overdue"] is True
    inbox = runtime.edge.get_command(command["commandUid"])
    assert inbox["state"] == "RECOVERY_REQUIRED"
    assert inbox["last_error"] == "MCU_RESULT_OVERDUE"
    stages = {
        row["stage"]
        for row in runtime.edge._conn.execute(
            "SELECT stage FROM command_observation WHERE command_uid=?",
            (command["commandUid"],),
        ).fetchall()
    }
    assert "FAILED" not in stages

    runtime.processor.process_mcu_event(_compat_result(command))

    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "COMPLETED"
    )
    assert runtime.edge.get_work_slot() is None
    assert len(
        [
            row
            for row in runtime.edge.list_pending_events(limit=100)
            if row["event_type"] == "CLEAN_COMPLETE"
        ]
    ) == 1
    runtime.updater.close()
    runtime.edge.close()


def test_clean_post_arm_uart_error_restart_keeps_recovery_lock(
    tmp_path,
    monkeypatch,
):
    ticks = {"milliseconds": 10_000}
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:clean-post-arm-restart",
    )
    runtime = _runtime(tmp_path)
    command = _start_clean_with_post_arm_uart_error(runtime)
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    restarted_work = WorkManager(
        runtime.edge,
        runtime.uart,
        None,
        runtime.photos,
        job_safety=runtime.safety,
    )
    restarted_processor = CommandProcessor(
        runtime.edge,
        runtime.uart,
        restarted_work,
    )
    ticks["milliseconds"] += (
        retained["context"]["operation_window_ms"] + 1
    )

    assert restarted_work.expire_fixed_frame_work() is True
    locked = runtime.edge.get_work_slot()
    assert locked is not None
    assert locked["work_state"] == "RECOVERY_REQUIRED"
    assert locked["context"]["phase"] == (
        "FIXED_FRAME_STATE_CORRUPT_RECOVERY_REQUIRED"
    )
    assert locked["context"]["fixed_frame_recovery_error"] == (
        "START_ACTION_LIVE_PROOF_INVALID"
    )
    with pytest.raises(
        JobSafetyError,
        match="cannot be bound to its original command",
    ):
        restarted_processor.process_mcu_event(_compat_result(command))
    assert runtime.edge.get_work_slot() is not None
    assert [
        row
        for row in runtime.edge.list_pending_events(limit=100)
        if row["event_type"] == "CLEAN_COMPLETE"
    ] == []
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "ACTIVE"
    assert runtime.updater.get_status()["jobGateState"] == "LOCKED"
    runtime.updater.close()
    runtime.edge.close()


def _open_committed_fixed_frame_result_crash_window(
    runtime,
    *,
    work_type,
    wall_reference,
):
    """Commit the permanent DD/EF receipt but lose every local reply."""

    fault_client = FaultInjectingUpdaterClient(
        runtime.updater,
        live_commit_then_unavailable=2,
        get_unavailable=2,
    )
    safety = PermanentJobSafety(fault_client)
    work = WorkManager(
        runtime.edge,
        runtime.uart,
        None,
        runtime.photos,
        job_safety=safety,
    )
    processor = CommandProcessor(runtime.edge, runtime.uart, work)
    runtime.safety = safety
    runtime.work = work
    runtime.processor = processor

    if work_type == "DELIVERY":
        command = _start_delivery_with_post_arm_uart_error(
            runtime,
            wall_reference,
        )
        action_key = "DELIVERY:START:0"
        terminal_event_type = "DELIVERY_COMPLETE"
    else:
        command = _start_clean_with_post_arm_uart_error(runtime)
        action_key = "CLEAN:START:0"
        terminal_event_type = "CLEAN_COMPLETE"

    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "RECOVERY_REQUIRED"
    )
    compat_result = _compat_result(command)
    stored_frame = {
        "message_name": compat_result["message_name"],
        "message_type": compat_result["message_type"],
        "tx_sequence": compat_result["source_tx_sequence"],
        "payload": compat_result["payload"],
    }
    assert runtime.edge.receive_mcu_frame(stored_frame) == "ACCEPTED"
    pending = runtime.edge.list_pending_mcu_events()
    assert len(pending) == 1

    with pytest.raises(JobSafetyError) as raised:
        processor.process_mcu_event(pending[0])
    assert raised.value.code == "JOB_GATE_UNAVAILABLE"

    slot = runtime.edge.get_work_slot()
    assert slot is not None
    safety_context = slot["context"]["job_safety"]
    action_record = safety_context["actions"][action_key]
    confirmation = safety_context["confirmations"][action_key]
    assert action_record["dispatch_result"] == "ARMED"
    assert confirmation["confirmed"] is False
    assert confirmation["confirmation_basis"] == (
        "LIVE_FIXED_FRAME_RESULT"
    )
    remote = runtime.updater.get_physical_action(
        {"actionUid": action_record["action_uid"]}
    )
    assert remote["state"] == "CONFIRMED"
    assert remote["confirmedOutcome"] == "EXECUTED"
    assert remote["confirmationBasis"] == "LIVE_FIXED_FRAME_RESULT"
    assert remote["evidenceDigestSha256"] == confirmation[
        "evidence_sha256"
    ]
    assert runtime.edge.list_pending_mcu_events()[0]["state"] == "PENDING"
    assert not [
        event
        for event in runtime.edge.list_pending_events(limit=100)
        if event["event_type"] == terminal_event_type
    ]
    return {
        "fault_client": fault_client,
        "safety": safety,
        "command": command,
        "pending": pending[0],
        "action_key": action_key,
        "action_uid": action_record["action_uid"],
        "permit_uid": safety_context["permit_uid"],
        "terminal_event_type": terminal_event_type,
    }


def _expire_committed_result_after_restart(runtime, scenario, ticks):
    recovered = runtime.edge.recover_interrupted_commands()
    assert recovered["physical_locked"] == 1
    assert recovered["physical_failed"] == 0
    restarted = WorkManager(
        runtime.edge,
        runtime.uart,
        None,
        runtime.photos,
        job_safety=scenario["safety"],
    )
    assert restarted._live_fixed_frame_dispatch_tokens == {}
    restarted_processor = CommandProcessor(
        runtime.edge,
        runtime.uart,
        restarted,
    )
    context = runtime.edge.get_work_slot()["context"]
    if scenario["terminal_event_type"] == "DELIVERY_COMPLETE":
        ticks["milliseconds"] = (
            context["delivery_result_deadline_monotonic_ms"] + 1
        )
    else:
        ticks["milliseconds"] += context["operation_window_ms"] + 1

    # Startup recovery runs before mcu_event_inbox replay.  The immutable
    # updater receipt and the locally frozen digest prove that this is an
    # adoptable crash window, not damaged state.
    assert restarted.expire_fixed_frame_work() is False
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    assert retained["work_state"] == "RECOVERY_REQUIRED"
    assert retained["context"]["phase"] == "START_RESULT_UNKNOWN"
    assert "fixed_frame_recovery_error" not in retained["context"]
    assert runtime.edge.get_command(
        scenario["command"]["commandUid"]
    )["last_error"] != "FIXED_FRAME_STATE_CORRUPT"
    return restarted, restarted_processor


def _finish_replayed_fixed_frame_result(
    runtime,
    scenario,
    restarted,
    restarted_processor,
):
    scenario["fault_client"].restore()
    assert restarted.reconcile_pending_physical_action_confirmations()
    frozen = runtime.edge.get_work_slot()["context"]["job_safety"][
        "confirmations"
    ][scenario["action_key"]]
    assert frozen["confirmed"] is True

    pending = scenario["pending"]
    restarted_processor.process_mcu_event(pending)
    assert runtime.edge.mark_mcu_event_processed(
        pending["mcu_boot_id"],
        pending["mcu_event_sequence"],
        pending["mcu_receive_generation"],
    )

    assert runtime.edge.get_command(
        scenario["command"]["commandUid"]
    )["state"] == "COMPLETED"
    terminal_events = [
        event
        for event in runtime.edge.list_pending_events(limit=100)
        if event["event_type"] == scenario["terminal_event_type"]
    ]
    assert len(terminal_events) == 1
    assert runtime.edge.list_pending_mcu_events() == []
    assert runtime.edge.get_work_slot() is None
    action = runtime.updater.get_physical_action(
        {"actionUid": scenario["action_uid"]}
    )
    assert action["state"] == "CONFIRMED"
    assert action["confirmedOutcome"] == "EXECUTED"
    permit = runtime.updater.get_job_permit(
        {"permitUid": scenario["permit_uid"]}
    )
    assert permit["state"] == "COMPLETED"
    assert runtime.updater.get_status()["jobGateState"] == "OPEN"


@pytest.mark.parametrize("work_type", ["DELIVERY", "CLEAN"])
def test_committed_dd_ef_receipt_survives_restart_expiry_before_replay(
    tmp_path,
    monkeypatch,
    work_type,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    ticks = {"milliseconds": 100_000}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: f"linux:committed-{work_type.lower()}-receipt",
    )
    runtime = _runtime(tmp_path)
    scenario = _open_committed_fixed_frame_result_crash_window(
        runtime,
        work_type=work_type,
        wall_reference=wall_reference,
    )
    restarted, restarted_processor = _expire_committed_result_after_restart(
        runtime,
        scenario,
        ticks,
    )

    _finish_replayed_fixed_frame_result(
        runtime,
        scenario,
        restarted,
        restarted_processor,
    )
    runtime.updater.close()
    runtime.edge.close()


def test_committed_dd_receipt_rejects_changed_payload_then_original_converges(
    tmp_path,
    monkeypatch,
):
    wall_reference = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    ticks = {"milliseconds": 100_000}
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: wall_reference,
    )
    monkeypatch.setattr(
        "work_manager._monotonic_ms",
        lambda: ticks["milliseconds"],
        raising=False,
    )
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:committed-dd-wrong-payload",
    )
    runtime = _runtime(tmp_path)
    scenario = _open_committed_fixed_frame_result_crash_window(
        runtime,
        work_type="DELIVERY",
        wall_reference=wall_reference,
    )
    restarted, restarted_processor = _expire_committed_result_after_restart(
        runtime,
        scenario,
        ticks,
    )

    changed = copy.deepcopy(scenario["pending"])
    changed["payload"]["postWeightGrams"] += 1
    with pytest.raises(JobSafetyError) as raised:
        restarted_processor.process_mcu_event(changed)
    assert raised.value.code == "PHYSICAL_ACTION_EVIDENCE_CONFLICT"
    assert runtime.edge.list_pending_mcu_events()[0]["payload"] == (
        scenario["pending"]["payload"]
    )
    assert runtime.edge.get_work_slot() is not None
    assert not [
        event
        for event in runtime.edge.list_pending_events(limit=100)
        if event["event_type"] == "DELIVERY_COMPLETE"
    ]
    remote = runtime.updater.get_physical_action(
        {"actionUid": scenario["action_uid"]}
    )
    frozen = runtime.edge.get_work_slot()["context"]["job_safety"][
        "confirmations"
    ][scenario["action_key"]]
    assert remote["evidenceDigestSha256"] == frozen["evidence_sha256"]
    assert frozen["confirmed"] is False

    _finish_replayed_fixed_frame_result(
        runtime,
        scenario,
        restarted,
        restarted_processor,
    )
    runtime.updater.close()
    runtime.edge.close()
