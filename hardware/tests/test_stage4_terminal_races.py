from __future__ import annotations

import copy
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from command_processor import CommandProcessor
from local_control import LocalControlUnavailable
from onenet_wire import canonical_payload_sha256
from work_manager import WorkManager
from tests.test_command_processor import (
    EndCleanControlUart,
    FakePhotoManager,
    FakeUart,
    clean_preunlock_event,
    make_real_job_safety,
    make_store,
    mark_configuration_applied,
    valid_service_command,
)
from tests.test_stage4_fixed_frame_convergence import (
    _compat_result,
    _receive_and_start,
    _runtime,
)


def _new_end_command(source: dict) -> dict:
    command = copy.deepcopy(source)
    command["commandUid"] = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    command["issuedAt"] = now.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    command["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["payloadSha256"] = canonical_payload_sha256(
        command["payload"]
    )
    return command


def _reach_retryable_end_with_confirmed_start(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    uart = EndCleanControlUart(
        end_result={"acked": False, "error": "BUSY", "fatal": False},
    )
    # A failed pre-unlock photo leaves the operation before the irreversible
    # unlock while still allowing the START action receipt to be confirmed.
    photos = FakePhotoManager(capture_result=False)
    work = WorkManager(
        store,
        uart,
        None,
        photos,
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    start = valid_service_command("start-clean-operation.service-wire.json")
    assert store.receive_command(
        start["commandUid"], start["commandType"], start
    ) == "ACCEPTED"
    assert processor.process_next()
    start_mcu_uid = uart.calls[-1][2]
    processor.process_mcu_event(
        clean_preunlock_event(
            start,
            start_mcu_uid,
            str(uuid.uuid4()),
        )
    )
    assert store.get_work_slot()["context"]["phase"] == (
        "PREUNLOCK_PHOTO_BLOCKED"
    )

    first_end = valid_service_command(
        "end-clean-before-unlock.service-wire.json"
    )
    assert store.receive_command(
        first_end["commandUid"],
        first_end["commandType"],
        first_end,
    ) == "ACCEPTED"
    assert processor.process_next()

    first_end_row = store.get_command(first_end["commandUid"])
    assert first_end_row["state"] == "FAILED"
    assert first_end_row["last_error"] == "BUSY"
    slot = store.get_work_slot()
    assert slot["context"]["phase"] == "END_BEFORE_UNLOCK_RETRYABLE"
    intent = slot["context"]["end_before_unlock"]
    assert intent["state"] == "RETRYABLE_NOT_ACCEPTED"
    assert intent["command_uid"] == first_end["commandUid"]
    return (
        store,
        updater,
        uart,
        work,
        processor,
        start,
        first_end,
        intent["mcu_command_uid"],
    )


def test_known_not_accepted_end_allows_new_exact_intent_to_take_over(
    tmp_path,
) -> None:
    (
        store,
        updater,
        uart,
        _work,
        processor,
        start,
        first_end,
        first_end_mcu_uid,
    ) = _reach_retryable_end_with_confirmed_start(tmp_path)
    try:
        replacement = _new_end_command(first_end)
        uart.end_result = {"acked": True, "disposition": "ACCEPTED"}
        assert store.receive_command(
            replacement["commandUid"],
            replacement["commandType"],
            replacement,
        ) == "ACCEPTED"

        assert processor.process_next()

        assert store.get_command(first_end["commandUid"])["state"] == (
            "FAILED"
        )
        assert store.get_command(replacement["commandUid"])["state"] == (
            "COMPLETED"
        )
        end_calls = [
            call
            for call in uart.calls
            if call[0] == "END_CLEAN_BEFORE_UNLOCK"
        ]
        assert len(end_calls) == 2
        assert end_calls[0][2] == first_end_mcu_uid
        assert end_calls[1][2] == first_end_mcu_uid
        assert end_calls[1][1]["operationUid"] == (
            end_calls[0][1]["operationUid"]
        )
        assert end_calls[1][1]["portNo"] == end_calls[0][1]["portNo"]
        assert end_calls[1][1]["reason"] == end_calls[0][1]["reason"]
        assert store.get_work_slot() is None
        permit = updater.get_job_permit(
            {"permitUid": start["commandUid"]}
        )
        assert permit["state"] == "COMPLETED"
        assert permit["completionOutcome"] == "CANCELLED"
        assert updater.get_status()["jobGateState"] == "OPEN"
    finally:
        updater.close()
        store.close()


@pytest.mark.parametrize(
    ("changed_field", "expected_error"),
    [
        ("reason", "STATE_CONFLICT"),
        ("operationUid", "UNKNOWN_WORK"),
        ("portNo", "UNKNOWN_WORK"),
    ],
)
def test_known_not_accepted_end_rejects_changed_business_identity(
    tmp_path,
    changed_field,
    expected_error,
) -> None:
    (
        store,
        updater,
        uart,
        work,
        _processor,
        _start,
        first_end,
        first_end_mcu_uid,
    ) = _reach_retryable_end_with_confirmed_start(tmp_path)
    try:
        conflicting = _new_end_command(first_end)
        if changed_field == "reason":
            conflicting["payload"]["reason"] = "DIFFERENT_REASON"
        elif changed_field == "operationUid":
            conflicting["payload"]["operationUid"] = str(uuid.uuid4())
            conflicting["target"]["uid"] = conflicting["payload"][
                "operationUid"
            ]
        else:
            conflicting["payload"]["portNo"] += 1
        conflicting["payloadSha256"] = canonical_payload_sha256(
            conflicting["payload"]
        )
        call_count = len(uart.calls)

        result = work.end_clean_before_unlock_command(conflicting)

        assert result == {"acked": False, "error": expected_error}
        assert len(uart.calls) == call_count
        retained = store.get_work_slot()["context"]
        assert retained["phase"] == "END_BEFORE_UNLOCK_RETRYABLE"
        assert retained["end_before_unlock"] == {
            "command_uid": first_end["commandUid"],
            "mcu_command_uid": first_end_mcu_uid,
            "reason": first_end["payload"]["reason"],
            "state": "RETRYABLE_NOT_ACCEPTED",
        }
    finally:
        updater.close()
        store.close()


def test_pending_end_is_claimable_after_blocked_preunlock_photo_fails(
    tmp_path,
) -> None:
    class BlockingFailedCleanOpenPhotos(FakePhotoManager):
        def __init__(self) -> None:
            super().__init__(capture_result=False)
            self.capture_started = threading.Event()
            self.release_capture = threading.Event()

        def capture_clean_open_photos(self, work_uid):
            self.capture_started.set()
            if not self.release_capture.wait(5):
                raise AssertionError(
                    "timed out waiting to release failed clean photo"
                )
            return self._capture("clean_open", work_uid)

    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    uart = FakeUart()
    photos = BlockingFailedCleanOpenPhotos()
    work = WorkManager(
        store,
        uart,
        None,
        photos,
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    start = valid_service_command("start-clean-operation.service-wire.json")
    assert store.receive_command(
        start["commandUid"], start["commandType"], start
    ) == "ACCEPTED"
    assert processor.process_next()
    start_mcu_uid = uart.calls[-1][2]
    event_errors: list[BaseException] = []

    def process_preunlock_event() -> None:
        try:
            processor.process_mcu_event(
                clean_preunlock_event(
                    start,
                    start_mcu_uid,
                    str(uuid.uuid4()),
                )
            )
        except BaseException as error:
            event_errors.append(error)

    event_thread = threading.Thread(target=process_preunlock_event)
    event_thread.start()
    try:
        assert photos.capture_started.wait(3)
        end = valid_service_command(
            "end-clean-before-unlock.service-wire.json"
        )
        assert store.receive_command(
            end["commandUid"], end["commandType"], end
        ) == "ACCEPTED"
        assert store.get_command(end["commandUid"])["state"] == "PENDING"

        photos.release_capture.set()
        event_thread.join(3)
        assert not event_thread.is_alive()
        assert event_errors == []
        retained = store.get_work_slot()
        assert retained is not None
        assert retained["context"]["phase"] == (
            "PREUNLOCK_PHOTO_BLOCKED"
        )
        assert [call[0] for call in uart.calls] == [
            "START_CLEAN_OPERATION"
        ]

        assert processor.process_next()

        assert store.get_command(end["commandUid"])["state"] == (
            "COMPLETED"
        )
        assert store.get_work_slot() is None
        assert [call[0] for call in uart.calls] == [
            "START_CLEAN_OPERATION",
            "END_CLEAN_BEFORE_UNLOCK",
        ]
        assert "UNLOCK_CLEAN_DOOR" not in {
            call[0] for call in uart.calls
        }
        permit = updater.get_job_permit(
            {"permitUid": start["commandUid"]}
        )
        assert permit["state"] == "COMPLETED"
        assert permit["completionOutcome"] == "CANCELLED"
    finally:
        photos.release_capture.set()
        event_thread.join(3)
        updater.close()
        store.close()


class _TemporarilyUnavailableCompletionClient:
    def __init__(self, delegate, *, failed_requests: int) -> None:
        self.delegate = delegate
        self.failed_requests = failed_requests
        self.completion_requests = 0

    def request(self, action: str, payload: dict) -> dict:
        if action == "COMPLETE_JOB":
            self.completion_requests += 1
            if self.failed_requests > 0:
                self.failed_requests -= 1
                raise LocalControlUnavailable(
                    "simulated updater completion outage"
                )
        return self.delegate.request(action, payload)


@pytest.mark.parametrize(
    ("example_name", "completion_event_type"),
    [
        (
            "start-delivery-session.service-wire.json",
            "DELIVERY_COMPLETE",
        ),
        (
            "start-clean-operation.service-wire.json",
            "CLEAN_COMPLETE",
        ),
    ],
)
def test_duplicate_fixed_frame_terminal_does_not_repeat_business_effects(
    tmp_path,
    example_name,
    completion_event_type,
) -> None:
    runtime = _runtime(tmp_path)
    completion_client = _TemporarilyUnavailableCompletionClient(
        runtime.client,
        # PermanentJobSafety retries each call twice. Keep both the original
        # terminal event and its duplicate unable to finish the permit.
        failed_requests=4,
    )
    runtime.safety._client = completion_client
    command = _receive_and_start(runtime, example_name)
    result = _compat_result(command)

    runtime.processor.process_mcu_event(result)

    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "COMPLETED"
    )
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    assert retained["context"]["phase"] == "COMPLETING"
    photos_after_first = list(runtime.photos.captured)
    events_after_first = [
        row
        for row in runtime.edge.list_pending_events(limit=100)
        if row["event_type"] == completion_event_type
    ]
    assert len(events_after_first) == 1

    runtime.processor.process_mcu_event(copy.deepcopy(result))

    assert runtime.edge.get_work_slot() is not None
    assert runtime.photos.captured == photos_after_first
    events_after_duplicate = [
        row
        for row in runtime.edge.list_pending_events(limit=100)
        if row["event_type"] == completion_event_type
    ]
    assert [row["event_uid"] for row in events_after_duplicate] == [
        events_after_first[0]["event_uid"]
    ]
    assert completion_client.completion_requests == 4

    assert runtime.work.reconcile_pending_job_safety_completion()
    assert runtime.edge.get_work_slot() is None
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "COMPLETED"
    assert permit["completionOutcome"] == "SUCCEEDED"
    runtime.updater.close()
    runtime.edge.close()


def test_overdue_check_preserves_committed_delivery_terminal_idempotence(
    tmp_path,
) -> None:
    runtime = _runtime(tmp_path)
    completion_client = _TemporarilyUnavailableCompletionClient(
        runtime.client,
        # Keep both the first DD and its replay unable to finish the permanent
        # permit. PermanentJobSafety performs two bounded attempts per call.
        failed_requests=4,
    )
    runtime.safety._client = completion_client
    command = _receive_and_start(
        runtime,
        "start-delivery-session.service-wire.json",
    )
    result = _compat_result(command)

    runtime.processor.process_mcu_event(result)

    retained = runtime.edge.get_work_slot()
    assert retained is not None
    assert retained["context"]["phase"] == "COMPLETING"
    assert isinstance(
        retained["context"]["job_safety"].get("pending_completion"),
        dict,
    )
    completion_events = [
        row
        for row in runtime.edge.list_pending_events(limit=100)
        if row["event_type"] == "DELIVERY_COMPLETE"
    ]
    assert len(completion_events) == 1
    first_event_uid = completion_events[0]["event_uid"]

    # Model the main-loop ordering after the diagnostic result deadline has
    # elapsed: expiry runs before receipt reconciliation or another queued DD.
    context = retained["context"]
    context["delivery_result_deadline_monotonic_ms"] = 1
    runtime.edge.update_work_context(retained["work_uid"], context)
    assert runtime.work.expire_fixed_frame_work() is False
    after_expiry = runtime.edge.get_work_slot()
    assert after_expiry is not None
    assert after_expiry["context"]["phase"] == "COMPLETING"

    runtime.processor.process_mcu_event(copy.deepcopy(result))

    after_replay = runtime.edge.get_work_slot()
    assert after_replay is not None
    assert after_replay["context"]["phase"] == "COMPLETING"
    replayed_events = [
        row
        for row in runtime.edge.list_pending_events(limit=100)
        if row["event_type"] == "DELIVERY_COMPLETE"
    ]
    assert [row["event_uid"] for row in replayed_events] == [
        first_event_uid
    ]
    assert completion_client.completion_requests == 4
    runtime.updater.close()
    runtime.edge.close()


def test_overdue_check_defers_failed_terminal_to_completion_reconciler(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "work_manager._system_boot_identity",
        lambda: "linux:failed-terminal-receipt",
    )
    runtime = _runtime(tmp_path)
    completion_client = _TemporarilyUnavailableCompletionClient(
        runtime.client,
        failed_requests=2,
    )
    runtime.safety._client = completion_client

    def reject_before_dispatch(message_name, values, **kwargs):
        del values, kwargs
        return {
            "acked": False,
            "error": "COMMAND_EXPIRED",
            "message_name": message_name,
            "mcu_command_uid": None,
        }

    runtime.uart.send_command_before_deadline = reject_before_dispatch
    command = _receive_and_start(
        runtime,
        "start-delivery-session.service-wire.json",
    )

    assert runtime.edge.get_command(command["commandUid"])["state"] == (
        "FAILED"
    )
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    context = retained["context"]
    assert context["phase"] == "START_FAILED"
    pending = context["job_safety"]["pending_completion"]
    assert pending["outcome"] == "FAILED"
    assert pending["physical_outcome"] == "NOT_EXECUTED"

    # Even an elapsed diagnostic deadline cannot reinterpret a frozen failed
    # terminal as damaged state. The permanent receipt reconciler owns it.
    context["delivery_result_deadline_monotonic_ms"] = 1
    context["delivery_result_deadline_boot_identity"] = (
        "linux:failed-terminal-receipt"
    )
    runtime.edge.update_work_context(retained["work_uid"], context)
    assert runtime.work.expire_fixed_frame_work() is False
    after_expiry = runtime.edge.get_work_slot()
    assert after_expiry is not None
    assert after_expiry["context"]["phase"] == "START_FAILED"
    assert after_expiry["context"]["job_safety"][
        "pending_completion"
    ] == pending

    assert runtime.work.reconcile_pending_job_safety_completion() is True
    assert runtime.edge.get_work_slot() is None
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "COMPLETED"
    assert permit["completionOutcome"] == "FAILED"
    runtime.updater.close()
    runtime.edge.close()


def test_late_dd_after_overdue_recovery_completes_original_work(
    tmp_path,
) -> None:
    runtime = _runtime(tmp_path)
    command = _receive_and_start(
        runtime,
        "start-delivery-session.service-wire.json",
    )
    slot = runtime.edge.get_work_slot()
    context = slot["context"]
    context["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    context["delivery_result_deadline_monotonic_ms"] = 1
    runtime.edge.update_work_context(slot["work_uid"], context)

    assert runtime.work.expire_fixed_frame_work()

    overdue = runtime.edge.get_command(command["commandUid"])
    assert overdue["state"] == "RECOVERY_REQUIRED"
    assert overdue["last_error"] == "MCU_RESULT_OVERDUE"
    retained = runtime.edge.get_work_slot()
    assert retained is not None
    action_uid = retained["context"]["job_safety"]["actions"][
        "DELIVERY:START:0"
    ]["action_uid"]
    assert retained["context"]["phase"] == (
        "FIXED_FRAME_RESULT_OVERDUE_RECOVERY_REQUIRED"
    )
    assert "pending_completion" not in retained["context"]["job_safety"]

    runtime.processor.process_mcu_event(_compat_result(command))

    completed = runtime.edge.get_command(command["commandUid"])
    assert completed["state"] == "COMPLETED"
    assert completed["result"]["resultOverdue"] is True
    events = [
        row
        for row in runtime.edge.list_pending_events(limit=100)
        if row["event_type"] == "DELIVERY_COMPLETE"
    ]
    assert len(events) == 1
    assert runtime.edge.get_work_slot() is None
    action = runtime.updater.get_physical_action(
        {"actionUid": action_uid}
    )
    assert action["state"] == "CONFIRMED"
    assert action["confirmedOutcome"] == "EXECUTED"
    assert action["confirmationBasis"] == "LIVE_FIXED_FRAME_RESULT"
    permit = runtime.updater.get_job_permit(
        {"permitUid": runtime.client.permit_uids[0]}
    )
    assert permit["state"] == "COMPLETED"
    assert permit["completionOutcome"] == "SUCCEEDED"
    assert runtime.updater.get_status()["jobGateState"] == "OPEN"
    runtime.updater.close()
    runtime.edge.close()
