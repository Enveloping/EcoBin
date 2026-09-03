from __future__ import annotations

import copy

import pytest

from job_safety import DisabledJobSafety, JobSafetyError, PermanentJobSafety
from local_control import LocalControlRemoteError, LocalControlUnavailable
from updater_store import UpdaterStore, UpdaterStoreError
from work_manager import WorkManager


COMMAND_UID = "10000000-0000-4000-8000-000000000001"
WORK_UID = "20000000-0000-4000-8000-000000000001"
ACTION_UID = "30000000-0000-4000-8000-000000000001"
AUTHORIZE_ACTION_UID = "30000000-0000-4000-8000-000000000002"
ACTION_KEY = "DELIVERY:START:0"
DISPATCH_TOKEN = "stage4-test-dispatch-token-0123456789abcdef"


def _activate_candidate(updater: UpdaterStore) -> None:
    status = updater.get_status()
    updater.activate_stage4_job_gate(
        {
            "operationUid": "90000000-0000-4000-8000-000000000004",
            "evidenceDigest": "f" * 64,
            "expectedManagementStateSequence": status[
                "managementStateSequence"
            ],
        }
    )


class ContextStore:
    def __init__(self) -> None:
        self.contexts: list[dict] = []

    def update_work_context(self, work_uid: str, context: dict) -> None:
        assert work_uid == WORK_UID
        self.contexts.append(copy.deepcopy(context))

    def save_fixed_frame_self_test(self, result: dict) -> None:
        del result


class StoreClient:
    def __init__(
        self,
        store: UpdaterStore,
        *,
        lose_arm_responses: int = 0,
        arm_unavailable: bool = False,
        abort_unavailable: bool = False,
        after_arm=None,
    ) -> None:
        self.store = store
        self.lose_arm_responses = lose_arm_responses
        self.arm_unavailable = arm_unavailable
        self.abort_unavailable = abort_unavailable
        self.after_arm = after_arm
        self.prepare_calls: list[dict] = []
        self.arm_calls: list[dict] = []
        self.cancel_calls: list[dict] = []
        self.abort_calls: list[dict] = []
        self.calls: list[str] = []

    def request(self, action: str, payload: dict) -> dict:
        self.calls.append(action)
        if action == "PREPARE_PHYSICAL_ACTION":
            self.prepare_calls.append(dict(payload))
        if action == "ARM_PHYSICAL_ACTION":
            self.arm_calls.append(dict(payload))
            if self.arm_unavailable:
                raise LocalControlUnavailable("simulated updater outage")
        if action == "ABORT_PHYSICAL_ACTION_DISPATCH":
            self.abort_calls.append(dict(payload))
            if self.abort_unavailable:
                raise LocalControlUnavailable("simulated abort outage")
        if action == "CANCEL_PREPARED_PHYSICAL_ACTION":
            self.cancel_calls.append(dict(payload))
        operations = {
            "REQUEST_JOB_PERMIT": self.store.request_job_permit,
            "BEGIN_JOB": self.store.begin_job,
            "GET_JOB_PERMIT": self.store.get_job_permit,
            "ABANDON_JOB_PERMIT": self.store.abandon_job_permit,
            "COMPLETE_JOB": self.store.complete_job,
            "PREPARE_PHYSICAL_ACTION": self.store.prepare_physical_action,
            "ARM_PHYSICAL_ACTION": self.store.arm_physical_action,
            "CANCEL_PREPARED_PHYSICAL_ACTION": (
                self.store.cancel_prepared_physical_action
            ),
            "ABORT_PHYSICAL_ACTION_DISPATCH": (
                self.store.abort_physical_action_dispatch
            ),
            "GET_PHYSICAL_ACTION": self.store.get_physical_action,
            "CONFIRM_PHYSICAL_ACTION": self.store.confirm_physical_action,
            "CONFIRM_LIVE_PHYSICAL_ACTION_RESULT": (
                self.store.confirm_live_physical_action_result
            ),
        }
        try:
            result = operations[action](payload)
        except UpdaterStoreError as error:
            raise LocalControlRemoteError(
                error.code,
                str(error),
                "40000000-0000-4000-8000-000000000001",
            ) from error
        if action == "ARM_PHYSICAL_ACTION":
            if self.after_arm is not None:
                self.after_arm()
            if self.lose_arm_responses > 0:
                self.lose_arm_responses -= 1
                raise LocalControlUnavailable("simulated response loss")
        return result


class GatedUart:
    def __init__(self, *, result: dict | None = None) -> None:
        self.result = result or {
            "acked": True,
            "disposition": "ACCEPTED",
            "tx_sequence": 7,
            "mcu_boot_id": 11,
        }
        self.gate_calls = 0
        self.write_count = 0

    def send_command_before_deadline(
        self,
        message_name,
        values,
        *,
        mcu_command_uid,
        dispatch_deadline_monotonic,
        dispatch_gate,
    ):
        del values, dispatch_deadline_monotonic
        self.gate_calls += 1
        dispatch_gate()
        self.write_count += 1
        return {
            "message_name": message_name,
            "mcu_command_uid": mcu_command_uid,
            **self.result,
        }


class ClosedUart(GatedUart):
    def send_command_before_deadline(
        self,
        message_name,
        values,
        *,
        mcu_command_uid,
        dispatch_deadline_monotonic,
        dispatch_gate,
    ):
        del values, dispatch_deadline_monotonic, dispatch_gate
        return {
            "acked": False,
            "error": "UART_CLOSED",
            "fatal": False,
            "message_name": message_name,
            "mcu_command_uid": mcu_command_uid,
        }


class DeadlineBeforeFirstWriteUart(GatedUart):
    def send_command_before_deadline(
        self,
        message_name,
        values,
        *,
        mcu_command_uid,
        dispatch_deadline_monotonic,
        dispatch_gate,
    ):
        del values, dispatch_deadline_monotonic
        self.gate_calls += 1
        dispatch_gate()
        return {
            "acked": False,
            "error": "COMMAND_EXPIRED",
            "fatal": False,
            "message_name": message_name,
            "mcu_command_uid": mcu_command_uid,
            "uart_write_attempted": False,
        }


class UngatedAckUart(GatedUart):
    """Broken adapter: claims success without invoking the required gate."""

    def send_command_before_deadline(
        self,
        message_name,
        values,
        *,
        mcu_command_uid,
        dispatch_deadline_monotonic,
        dispatch_gate,
    ):
        del values, dispatch_deadline_monotonic, dispatch_gate
        self.write_count += 1
        return {
            "acked": True,
            "disposition": "ACCEPTED",
            "message_name": message_name,
            "mcu_command_uid": mcu_command_uid,
        }


class GatedFixedFrameQuery:
    compatibility_mode = True

    def __init__(self) -> None:
        self.gate_calls = 0
        self.write_count = 0

    def query_self_test(
        self,
        *,
        timeout_ms,
        on_result,
        dispatch_gate,
    ):
        del timeout_ms, on_result
        self.gate_calls += 1
        dispatch_gate()
        self.write_count += 1
        return {"queryStatus": "OK"}


def _manager(
    tmp_path,
    *,
    lose_arm_responses=0,
    arm_unavailable=False,
    abort_unavailable=False,
    after_arm=None,
    uart=None,
):
    updater = UpdaterStore(
        tmp_path / "updater.db",
        release_version="stage4-test",
        enable_stage4_candidate=True,
    )
    updater.initialize()
    _activate_candidate(updater)
    client = StoreClient(
        updater,
        lose_arm_responses=lose_arm_responses,
        arm_unavailable=arm_unavailable,
        abort_unavailable=abort_unavailable,
        after_arm=after_arm,
    )
    safety = PermanentJobSafety(client)
    command = {
        "commandUid": COMMAND_UID,
        "commandType": "START_DELIVERY_SESSION",
        "payload": {"sessionUid": WORK_UID, "portNo": 1},
    }
    permit = safety.request_job(
        command,
        work_type="DELIVERY",
        work_uid=WORK_UID,
    )
    safety.begin_job(
        permit,
        begin_uid=WORK_UID,
        digest=permit.request_digest_sha256,
    )
    context = {
        "job_safety": {
            "permit_uid": permit.permit_uid,
            "work_uid": permit.work_uid,
            "command_uid": permit.command_uid,
            "work_type": permit.work_type,
            "request_digest_sha256": permit.request_digest_sha256,
            "begin_uid": WORK_UID,
            "completion_uid": COMMAND_UID,
            "actions": {},
        }
    }
    context_store = ContextStore()
    uart = uart or GatedUart()
    manager = WorkManager(
        context_store,
        uart,
        None,
        None,
        job_safety=safety,
    )
    return updater, client, context_store, uart, manager, context


def _send(
    manager: WorkManager,
    context: dict,
    *,
    dispatch_deadline_monotonic_cap: float | None = None,
) -> dict:
    return manager._send_physical_command(
        context,
        "START_DELIVERY_SESSION",
        {"sessionUid": WORK_UID, "portNo": 1},
        mcu_command_uid=ACTION_UID,
        action_key=ACTION_KEY,
        action_kind="START_DELIVERY_SESSION",
        not_after="2099-01-01T00:00:00.000Z",
        dispatch_deadline_monotonic_cap=(
            dispatch_deadline_monotonic_cap
        ),
    )


def _contains_key(value, forbidden: str) -> bool:
    if isinstance(value, dict):
        return forbidden in value or any(
            _contains_key(item, forbidden) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def test_lost_arm_response_retries_same_token_and_writes_once(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("work_manager._remaining_until", lambda _value: 10_000)
    updater, client, context_store, uart, manager, context = _manager(
        tmp_path,
        lose_arm_responses=1,
    )

    result = _send(manager, context)

    assert result["acked"] is True
    assert uart.gate_calls == 1
    assert uart.write_count == 1
    assert len(client.arm_calls) == 2
    assert (
        client.prepare_calls[0]["dispatchAttemptToken"]
        == client.arm_calls[0]["dispatchAttemptToken"]
    )
    assert (
        client.arm_calls[0]["dispatchAttemptToken"]
        == client.arm_calls[1]["dispatchAttemptToken"]
    )
    assert updater.get_physical_action({"actionUid": ACTION_UID})[
        "state"
    ] == "ARMED"
    assert not _contains_key(context, "dispatchAttemptToken")
    assert not _contains_key(context_store.contexts, "dispatchAttemptToken")


def test_two_lost_arm_responses_abort_before_uart_write(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("work_manager._remaining_until", lambda _value: 10_000)
    updater, client, _context_store, uart, manager, context = _manager(
        tmp_path,
        lose_arm_responses=2,
    )

    with pytest.raises(JobSafetyError, match="JOB_GATE_UNAVAILABLE"):
        _send(manager, context)

    assert uart.gate_calls == 1
    assert uart.write_count == 0
    assert len(client.arm_calls) == 2
    assert client.prepare_calls[0]["dispatchAttemptToken"] == (
        client.arm_calls[0]["dispatchAttemptToken"]
    )
    action = updater.get_physical_action({"actionUid": ACTION_UID})
    assert action["state"] == "CONFIRMED"
    assert action["confirmedOutcome"] == "NOT_EXECUTED"
    assert action["confirmationBasis"] == "LIVE_DISPATCH_NOT_WRITTEN"


def test_deadline_after_arm_but_before_first_write_aborts_exact_action(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("work_manager._remaining_until", lambda _value: 10_000)
    uart = DeadlineBeforeFirstWriteUart()
    updater, _client, _contexts, _, manager, context = _manager(
        tmp_path,
        uart=uart,
    )

    result = _send(manager, context)

    assert result["acked"] is False
    assert result["error"] == "COMMAND_EXPIRED"
    assert result["physicalEffect"] == "NOT_EXECUTED"
    assert uart.gate_calls == 1
    assert uart.write_count == 0
    action = updater.get_physical_action({"actionUid": ACTION_UID})
    assert action["state"] == "CONFIRMED"
    assert action["confirmedOutcome"] == "NOT_EXECUTED"
    assert action["confirmationBasis"] == "LIVE_DISPATCH_NOT_WRITTEN"
    assert not _contains_key(context, "dispatchAttemptToken")


def test_deadline_crossing_during_arm_aborts_with_same_token_before_write(
    tmp_path,
    monkeypatch,
) -> None:
    ticks = {"value": 100.0}
    monkeypatch.setattr("work_manager._remaining_until", lambda _value: 1_000)
    monkeypatch.setattr(
        "work_manager.time.monotonic",
        lambda: ticks["value"],
    )
    updater, client, _contexts, uart, manager, context = _manager(
        tmp_path,
        after_arm=lambda: ticks.update(value=102.0),
    )

    with pytest.raises(JobSafetyError) as raised:
        _send(manager, context)

    assert raised.value.code == "COMMAND_EXPIRED"
    assert uart.gate_calls == 1
    assert uart.write_count == 0
    assert len(client.arm_calls) == 1
    assert len(client.abort_calls) == 1
    assert client.prepare_calls[0]["dispatchAttemptToken"] == (
        client.arm_calls[0]["dispatchAttemptToken"]
    )
    assert client.abort_calls[0]["dispatchAttemptToken"] == (
        client.arm_calls[0]["dispatchAttemptToken"]
    )
    action = updater.get_physical_action({"actionUid": ACTION_UID})
    assert action["state"] == "CONFIRMED"
    assert action["confirmedOutcome"] == "NOT_EXECUTED"
    assert action["confirmationBasis"] == "LIVE_DISPATCH_NOT_WRITTEN"
    assert context["job_safety"]["confirmations"][ACTION_KEY][
        "confirmed"
    ] is True


def test_relative_operation_window_caps_untrusted_wall_clock_during_arm(
    tmp_path,
    monkeypatch,
) -> None:
    ticks = {"value": 100.0}
    # This models an unavailable trusted wall clock: the absolute timestamp
    # path can only provide its maximum fallback, while the clean operation
    # still owns a trustworthy local monotonic deadline.
    monkeypatch.setattr(
        "work_manager._remaining_until",
        lambda _value: 4_294_967_295,
    )
    monkeypatch.setattr(
        "work_manager.time.monotonic",
        lambda: ticks["value"],
    )
    updater, client, _contexts, uart, manager, context = _manager(
        tmp_path,
        after_arm=lambda: ticks.update(value=102.0),
    )

    with pytest.raises(JobSafetyError) as raised:
        _send(
            manager,
            context,
            dispatch_deadline_monotonic_cap=101.0,
        )

    assert raised.value.code == "COMMAND_EXPIRED"
    assert uart.gate_calls == 1
    assert uart.write_count == 0
    assert len(client.abort_calls) == 1
    action = updater.get_physical_action({"actionUid": ACTION_UID})
    assert action["state"] == "CONFIRMED"
    assert action["confirmedOutcome"] == "NOT_EXECUTED"
    assert action["confirmationBasis"] == "LIVE_DISPATCH_NOT_WRITTEN"


def test_unknown_abort_after_deadline_keeps_armed_action_without_write(
    tmp_path,
    monkeypatch,
) -> None:
    ticks = {"value": 100.0}
    monkeypatch.setattr("work_manager._remaining_until", lambda _value: 1_000)
    monkeypatch.setattr(
        "work_manager.time.monotonic",
        lambda: ticks["value"],
    )
    updater, client, _contexts, uart, manager, context = _manager(
        tmp_path,
        abort_unavailable=True,
        after_arm=lambda: ticks.update(value=102.0),
    )

    with pytest.raises(JobSafetyError) as raised:
        _send(manager, context)

    assert raised.value.code == "JOB_GATE_UNAVAILABLE"
    assert uart.gate_calls == 1
    assert uart.write_count == 0
    assert len(client.arm_calls) == 1
    assert len(client.abort_calls) == 2
    assert all(
        abort["dispatchAttemptToken"]
        == client.arm_calls[0]["dispatchAttemptToken"]
        for abort in client.abort_calls
    )
    assert client.prepare_calls[0]["dispatchAttemptToken"] == (
        client.arm_calls[0]["dispatchAttemptToken"]
    )
    action = updater.get_physical_action({"actionUid": ACTION_UID})
    assert action["state"] == "ARMED"
    assert action["confirmedOutcome"] is None
    assert context["job_safety"]["actions"][ACTION_KEY][
        "dispatch_result"
    ] == "ARMED"
    assert context["job_safety"].get("confirmations", {}) == {}


def test_fixed_frame_query_deadline_crossing_during_arm_aborts_before_write(
    tmp_path,
    monkeypatch,
) -> None:
    ticks = {"value": 100.0}
    monkeypatch.setattr(
        "work_manager.time.monotonic",
        lambda: ticks["value"],
    )
    fixed_uart = GatedFixedFrameQuery()
    updater, client, _contexts, _uart, manager, context = _manager(
        tmp_path,
        uart=fixed_uart,
        after_arm=lambda: ticks.update(value=102.0),
    )
    action = manager._prepare_physical_action(
        context,
        message_name="FIXED_FRAME_F0_QUERY",
        values={"portNo": 1},
        mcu_command_uid=ACTION_UID,
        action_key=ACTION_KEY,
        action_kind="FIXED_FRAME_SELF_TEST",
        dispatch_attempt_token=DISPATCH_TOKEN,
    )

    with pytest.raises(JobSafetyError) as raised:
        manager._query_fixed_frame_self_test_with_gate(
            context,
            action=action,
            action_key=ACTION_KEY,
            timeout_ms=1_000,
            dispatch_attempt_token=DISPATCH_TOKEN,
            dispatch_deadline_monotonic_cap=101.0,
        )

    assert raised.value.code == "COMMAND_EXPIRED"
    assert fixed_uart.gate_calls == 1
    assert fixed_uart.write_count == 0
    assert client.abort_calls[0]["dispatchAttemptToken"] == (
        client.arm_calls[0]["dispatchAttemptToken"]
    )
    assert client.prepare_calls[0]["dispatchAttemptToken"] == (
        client.arm_calls[0]["dispatchAttemptToken"]
    )
    result = updater.get_physical_action({"actionUid": ACTION_UID})
    assert result["state"] == "CONFIRMED"
    assert result["confirmedOutcome"] == "NOT_EXECUTED"
    assert result["confirmationBasis"] == "LIVE_DISPATCH_NOT_WRITTEN"


def test_uart_precheck_failure_cancels_only_prepared_action(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("work_manager._remaining_until", lambda _value: 10_000)
    updater, client, _context_store, uart, manager, context = _manager(
        tmp_path,
        uart=ClosedUart(),
    )

    result = _send(manager, context)

    assert result["physicalEffect"] == "NOT_EXECUTED"
    assert client.arm_calls == []
    assert client.prepare_calls[0]["dispatchAttemptToken"] == (
        client.cancel_calls[0]["dispatchAttemptToken"]
    )
    assert uart.write_count == 0
    action = updater.get_physical_action({"actionUid": ACTION_UID})
    assert action["state"] == "CONFIRMED"
    assert action["confirmationBasis"] == "PREPARED_NOT_ARMED"


def test_rolled_back_business_state_cannot_cancel_armed_action(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("work_manager._remaining_until", lambda _value: 10_000)
    timeout_uart = GatedUart(
        result={"acked": False, "error": "TIMEOUT", "fatal": False}
    )
    updater, _client, _context_store, uart, manager, context = _manager(
        tmp_path,
        uart=timeout_uart,
    )

    result = _send(manager, context)
    assert result["physicalEffect"] == "UNKNOWN"
    assert uart.write_count == 1
    action = updater.get_physical_action({"actionUid": ACTION_UID})
    assert action["state"] == "ARMED"

    restored = copy.deepcopy(context)
    restored["job_safety"]["actions"][ACTION_KEY][
        "preparation_result"
    ] = "UNKNOWN"
    restored["job_safety"]["actions"][ACTION_KEY][
        "dispatch_result"
    ] = "UNKNOWN"
    restored["job_safety"].pop("confirmations", None)

    assert manager._reconcile_never_dispatched_actions(
        restored,
        restored["job_safety"],
    ) is False
    action = updater.get_physical_action({"actionUid": ACTION_UID})
    assert action["state"] == "ARMED"
    assert action["confirmedOutcome"] is None


def test_permanent_prepared_state_requires_manual_recovery_after_restart(
    tmp_path,
) -> None:
    updater, client, _context_store, _uart, manager, context = _manager(
        tmp_path
    )
    manager._prepare_physical_action(
        context,
        message_name="START_DELIVERY_SESSION",
        values={"sessionUid": WORK_UID, "portNo": 1},
        mcu_command_uid=ACTION_UID,
        action_key=ACTION_KEY,
        action_kind="START_DELIVERY_SESSION",
        dispatch_attempt_token=DISPATCH_TOKEN,
    )

    assert manager._reconcile_never_dispatched_actions(
        context,
        context["job_safety"],
    ) is False
    action = updater.get_physical_action({"actionUid": ACTION_UID})
    assert action["state"] == "PREPARED"
    assert action["confirmedOutcome"] is None
    assert "CANCEL_PREPARED_PHYSICAL_ACTION" not in client.calls
    status = updater.get_status()
    assert status["activeJobPermitCount"] == 1
    assert status["unreconciledPhysicalActionCount"] == 1


def test_acked_uart_result_without_dispatch_gate_is_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("work_manager._remaining_until", lambda _value: 10_000)
    updater, _client, _contexts, uart, manager, context = _manager(
        tmp_path,
        uart=UngatedAckUart(),
    )

    with pytest.raises(JobSafetyError) as raised:
        _send(manager, context)

    assert raised.value.code == "UART_DISPATCH_GATE_BYPASSED"
    assert uart.gate_calls == 0
    action = updater.get_physical_action({"actionUid": ACTION_UID})
    assert action["state"] == "ARMED"
    assert action["confirmedOutcome"] is None
    assert context["job_safety"]["actions"][ACTION_KEY][
        "dispatch_result"
    ] == "ARMED_AFTER_GATE_BYPASS"
    assert context["job_safety"].get("confirmations", {}) == {}


def test_gate_bypass_with_failed_late_arm_survives_edge_rollback(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("work_manager._remaining_until", lambda _value: 10_000)
    updater, client, contexts, uart, manager, context = _manager(
        tmp_path,
        arm_unavailable=True,
        uart=UngatedAckUart(),
    )

    with pytest.raises(JobSafetyError) as raised:
        _send(manager, context)

    assert raised.value.code == "UART_DISPATCH_GATE_BYPASSED"
    assert uart.write_count == 1
    assert len(client.arm_calls) == 2
    assert context["job_safety"]["actions"][ACTION_KEY][
        "dispatch_result"
    ] == "GATE_BYPASSED_MAY_HAVE_WRITTEN"
    assert updater.get_physical_action({"actionUid": ACTION_UID})[
        "state"
    ] == "PREPARED"

    # Restore the last business snapshot from before the bypass marker was
    # persisted.  It cannot prove that the broken adapter did not write.
    pre_bypass_snapshots = [
        snapshot
        for snapshot in contexts.contexts
        if snapshot.get("job_safety", {})
        .get("actions", {})
        .get(ACTION_KEY, {})
        .get("preparation_result")
        == "PREPARED"
        and "dispatch_result"
        not in snapshot["job_safety"]["actions"][ACTION_KEY]
    ]
    assert pre_bypass_snapshots
    restored = pre_bypass_snapshots[-1]

    assert manager._reconcile_never_dispatched_actions(
        restored,
        restored["job_safety"],
    ) is False
    action = updater.get_physical_action({"actionUid": ACTION_UID})
    assert action["state"] == "PREPARED"
    assert action["confirmedOutcome"] is None
    assert "CANCEL_PREPARED_PHYSICAL_ACTION" not in client.calls
    status = updater.get_status()
    assert status["activeJobPermitCount"] == 1
    assert status["unreconciledPhysicalActionCount"] == 1


def test_disabled_safety_rejects_restored_managed_context_before_uart(
    monkeypatch,
) -> None:
    monkeypatch.setattr("work_manager._remaining_until", lambda _value: 10_000)
    context = {
        "job_safety": {
            "permit_uid": COMMAND_UID,
            "work_uid": WORK_UID,
            "command_uid": COMMAND_UID,
            "work_type": "DELIVERY",
            "request_digest_sha256": "a" * 64,
            "begin_uid": WORK_UID,
            "completion_uid": COMMAND_UID,
            "actions": {},
        }
    }
    uart = GatedUart()
    manager = WorkManager(
        ContextStore(),
        uart,
        None,
        None,
        job_safety=DisabledJobSafety(),
    )

    with pytest.raises(JobSafetyError) as raised:
        _send(manager, context)

    assert raised.value.code == "JOB_GATE_MODE_MISMATCH"
    assert uart.gate_calls == 0
    assert uart.write_count == 0
    assert context["job_safety"]["actions"] == {}


def test_delivery_safe_rejection_is_confirmed_as_failed_safe(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("work_manager._remaining_until", lambda _value: 10_000)
    updater, _client, _contexts, _uart, manager, context = _manager(tmp_path)
    assert _send(manager, context)["acked"] is True
    manager._confirm_action_from_mcu_event(
        context,
        expected_action_key=ACTION_KEY,
        expected_action_kind="START_DELIVERY_SESSION",
        event_type="WORK_PREOPEN_WEIGHT_READY",
        payload={
            "mcuCommandUid": ACTION_UID,
            "mcuBootId": 11,
            "mcuEventSequence": 7,
        },
    )
    context.update(
        {
            "port_no": 1,
            "round_index": 1,
            "phase": "WAITING_OPEN_COMMAND_RESULT",
            "authorize_mcu_command_uid": AUTHORIZE_ACTION_UID,
        }
    )
    result = manager._send_physical_command(
        context,
        "AUTHORIZE_DELIVERY_FIRST_OPEN",
        {"sessionUid": WORK_UID, "portNo": 1},
        mcu_command_uid=AUTHORIZE_ACTION_UID,
        action_key="DELIVERY:AUTHORIZE_OPEN:0",
        action_kind="OPEN_DELIVERY_DOOR",
        not_after="2099-01-01T00:00:00.000Z",
    )
    assert result["acked"] is True
    terminal_failures = []
    monkeypatch.setattr(
        manager,
        "_fail_active_job_at_safe_boundary",
        lambda **values: terminal_failures.append(values) or True,
    )

    manager._on_delivery_door_command_result(
        context,
        {
            "mcuBootId": 11,
            "mcuEventSequence": 8,
            "uptimeMs": 900,
            "mcuCommandUid": AUTHORIZE_ACTION_UID,
            "sessionUid": WORK_UID,
            "portNo": 1,
            "roundIndex": 1,
            "command": "OPEN",
            "outputStatus": "OUTPUT_REJECTED",
            "physicalDoorStateBasis": "NOT_OBSERVABLE",
            "faultCode": "INTERLOCK_ACTIVE",
        },
        WORK_UID,
    )

    action = updater.get_physical_action(
        {"actionUid": AUTHORIZE_ACTION_UID}
    )
    assert action["state"] == "CONFIRMED"
    assert action["confirmedOutcome"] == "FAILED_SAFE"
    assert action["confirmationBasis"] == "MCU_IDENTITY_BOUND_FACT"
    assert terminal_failures[0]["error_code"] == (
        "DELIVERY_OPEN_FAILED_SAFE"
    )
