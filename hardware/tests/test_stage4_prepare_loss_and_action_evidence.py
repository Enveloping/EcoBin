from __future__ import annotations

import copy
import uuid

import pytest

from job_safety import JobSafetyError
from local_control import LocalControlUnavailable
from tests.test_command_processor import (
    FakePhotoManager,
    FakeUart,
    clean_preunlock_event,
    make_real_job_safety,
    make_store,
    mark_configuration_applied,
    valid_service_command,
)
from tests.test_stage4_two_phase_dispatch import (
    ACTION_KEY,
    ACTION_UID,
    _manager,
    _send,
)
from tests.test_stage4_fixed_frame_convergence import (
    _runtime,
    valid_compat_service_command,
)
from work_manager import WorkManager


class _CommittedResponseLossClient:
    """Commit selected updater calls, then hide their replies."""

    def __init__(
        self,
        delegate,
        *,
        lose_prepare_responses: int = 0,
        lose_cancel_responses: int = 0,
        lose_get_responses: int = 0,
    ) -> None:
        self.delegate = delegate
        self.lose_prepare_responses = lose_prepare_responses
        self.lose_cancel_responses = lose_cancel_responses
        self.lose_get_responses = lose_get_responses
        self.prepare_requests: list[dict] = []
        self.cancel_requests: list[dict] = []
        self.get_requests: list[dict] = []

    def request(self, action: str, payload: dict) -> dict:
        if action == "GET_PHYSICAL_ACTION":
            self.get_requests.append(dict(payload))
            if self.lose_get_responses > 0:
                self.lose_get_responses -= 1
                raise LocalControlUnavailable(
                    "simulated physical-action read outage"
                )

        result = self.delegate.request(action, payload)
        if action == "PREPARE_PHYSICAL_ACTION":
            self.prepare_requests.append(dict(payload))
            if self.lose_prepare_responses > 0:
                self.lose_prepare_responses -= 1
                raise LocalControlUnavailable(
                    "simulated committed PREPARE response loss"
                )
        elif action == "CANCEL_PREPARED_PHYSICAL_ACTION":
            self.cancel_requests.append(dict(payload))
            if self.lose_cancel_responses > 0:
                self.lose_cancel_responses -= 1
                raise LocalControlUnavailable(
                    "simulated committed CANCEL response loss"
                )
        return result


def _assert_prepare_was_closed_without_uart(
    *, updater, base_client, client, uart, context
) -> None:
    assert uart.gate_calls == 0
    assert uart.write_count == 0
    assert base_client.arm_calls == []
    assert len(client.prepare_requests) == 2
    assert len(client.cancel_requests) >= 1

    prepare_token = client.prepare_requests[0]["dispatchAttemptToken"]
    assert all(
        request["dispatchAttemptToken"] == prepare_token
        for request in client.prepare_requests
    )
    assert all(
        request["dispatchAttemptToken"] == prepare_token
        for request in client.cancel_requests
    )

    remote = updater.get_physical_action({"actionUid": ACTION_UID})
    assert remote["state"] == "CONFIRMED"
    assert remote["confirmedOutcome"] == "NOT_EXECUTED"
    assert remote["confirmationBasis"] == "PREPARED_NOT_ARMED"
    assert remote["receiptUid"] == ACTION_UID

    local_action = context["job_safety"]["actions"][ACTION_KEY]
    local_confirmation = context["job_safety"]["confirmations"][
        ACTION_KEY
    ]
    assert local_action["dispatch_result"] == "CONFIRMED"
    assert local_confirmation == {
        "outcome": "NOT_EXECUTED",
        "evidence_sha256": remote["evidenceDigestSha256"],
        "confirmation_basis": "PREPARED_NOT_ARMED",
        "confirmed": True,
    }


def test_committed_prepare_with_two_lost_replies_is_cancelled_by_live_call(
    tmp_path,
) -> None:
    updater, base_client, _contexts, uart, manager, context = _manager(
        tmp_path
    )
    client = _CommittedResponseLossClient(
        base_client,
        lose_prepare_responses=2,
    )
    manager._job_safety._client = client
    try:
        result = _send(manager, context)

        assert result["acked"] is False
        assert result["physicalEffect"] == "NOT_EXECUTED"
        assert len(client.get_requests) == 1
        _assert_prepare_was_closed_without_uart(
            updater=updater,
            base_client=base_client,
            client=client,
            uart=uart,
            context=context,
        )
    finally:
        updater.close()


def test_committed_cancel_with_two_lost_replies_is_adopted_by_exact_get(
    tmp_path,
) -> None:
    updater, base_client, _contexts, uart, manager, context = _manager(
        tmp_path
    )
    client = _CommittedResponseLossClient(
        base_client,
        lose_prepare_responses=2,
        lose_cancel_responses=2,
    )
    manager._job_safety._client = client
    try:
        result = _send(manager, context)

        assert result["acked"] is False
        assert result["physicalEffect"] == "NOT_EXECUTED"
        assert len(client.cancel_requests) == 2
        # One read finds PREPARED; the second adopts the exact committed
        # PREPARED_NOT_ARMED receipt after both CANCEL replies were lost.
        assert len(client.get_requests) == 2
        _assert_prepare_was_closed_without_uart(
            updater=updater,
            base_client=base_client,
            client=client,
            uart=uart,
            context=context,
        )
    finally:
        updater.close()


def test_uncertain_prepare_with_unavailable_get_remains_prepared_and_locked(
    tmp_path,
) -> None:
    updater, base_client, _contexts, uart, manager, context = _manager(
        tmp_path
    )
    client = _CommittedResponseLossClient(
        base_client,
        lose_prepare_responses=2,
        # PermanentJobSafety performs two bounded reads before returning an
        # unavailable result to the still-live caller.
        lose_get_responses=2,
    )
    manager._job_safety._client = client
    try:
        with pytest.raises(JobSafetyError) as raised:
            _send(manager, context)

        assert raised.value.code == "JOB_GATE_UNAVAILABLE"
        assert len(client.get_requests) == 2
        assert client.cancel_requests == []
        assert base_client.cancel_calls == []
        assert base_client.arm_calls == []
        assert uart.gate_calls == 0
        assert uart.write_count == 0

        remote = updater.get_physical_action({"actionUid": ACTION_UID})
        assert remote["state"] == "PREPARED"
        assert remote["confirmedOutcome"] is None
        assert updater.get_status()["unreconciledPhysicalActionCount"] == 1
        local_action = context["job_safety"]["actions"][ACTION_KEY]
        assert local_action["preparation_result"] == "UNKNOWN"
        assert context["job_safety"].get("confirmations", {}) == {}
    finally:
        updater.close()


def test_same_process_retry_closes_uncertain_prepare_without_uart(
    tmp_path,
) -> None:
    updater, base_client, _contexts, uart, manager, context = _manager(
        tmp_path
    )
    client = _CommittedResponseLossClient(
        base_client,
        lose_prepare_responses=2,
        lose_get_responses=2,
    )
    manager._job_safety._client = client
    try:
        with pytest.raises(JobSafetyError) as raised:
            _send(manager, context)
        assert raised.value.code == "JOB_GATE_UNAVAILABLE"

        result = _send(manager, context)

        assert result["physicalEffect"] == "NOT_EXECUTED"
        assert uart.gate_calls == 0
        assert uart.write_count == 0
        tokens = {
            request["dispatchAttemptToken"]
            for request in client.prepare_requests
        }
        assert len(tokens) == 1
        remote = updater.get_physical_action({"actionUid": ACTION_UID})
        assert remote["state"] == "CONFIRMED"
        assert remote["confirmedOutcome"] == "NOT_EXECUTED"
        assert remote["confirmationBasis"] == "PREPARED_NOT_ARMED"
    finally:
        updater.close()


def test_initial_clean_unlock_retry_reuses_identity_then_fails_safe(
    tmp_path,
) -> None:
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    base_client = safety._client
    uart = FakeUart()
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    command = valid_service_command(
        "start-clean-operation.service-wire.json"
    )
    assert store.receive_command(
        command["commandUid"], command["commandType"], command
    ) == "ACCEPTED"
    assert work.start_clean_command(command)["acked"] is True
    start_action_uid = uart.calls[-1][2]
    calls_before_unlock = list(uart.calls)
    client = _CommittedResponseLossClient(
        base_client,
        lose_prepare_responses=2,
        lose_get_responses=2,
    )
    safety._client = client
    event = clean_preunlock_event(
        command,
        start_action_uid,
        str(uuid.uuid4()),
    )
    try:
        with pytest.raises(JobSafetyError) as raised:
            work.handle_mcu_event(event)
        assert raised.value.code == "JOB_GATE_UNAVAILABLE"
        first = store.get_work_slot()["context"]
        unlock_uid = first["unlock_mcu_command_uid"]
        unlock_key = first["unlock_action_key"]

        work.handle_mcu_event(event)

        assert uart.calls == calls_before_unlock
        assert store.get_work_slot() is None
        remote = updater.get_physical_action({"actionUid": unlock_uid})
        assert remote["confirmedOutcome"] == "NOT_EXECUTED"
        assert first["job_safety"]["actions"][unlock_key][
            "action_uid"
        ] == unlock_uid
    finally:
        updater.close()
        store.close()


def test_clean_reunlock_same_event_retry_keeps_action_identity(
    tmp_path,
) -> None:
    (
        store,
        updater,
        uart,
        work,
        command,
        _start_action_uid,
        unlock_action_uid,
        _action_key,
        action_sequence,
    ) = _clean_until_unlock(tmp_path)
    work.handle_mcu_event(
        _clean_lock_event(
            command,
            mcu_command_uid=unlock_action_uid,
            action_sequence=action_sequence,
            event_sequence=2,
            power_state="DEENERGIZED",
        )
    )
    calls_before_reunlock = list(uart.calls)
    base_client = work._job_safety._client
    client = _CommittedResponseLossClient(
        base_client,
        lose_prepare_responses=2,
        lose_get_responses=2,
    )
    work._job_safety._client = client
    event = {
        "message_name": "CLEAN_UNLOCK_REQUESTED",
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 3,
            "uptimeMs": 3_000,
            "operationUid": command["payload"]["operationUid"],
            "portNo": command["payload"]["portNo"],
            "cleanActionSequence": action_sequence + 1,
        },
    }
    try:
        with pytest.raises(JobSafetyError) as raised:
            work.handle_mcu_event(event)
        assert raised.value.code == "JOB_GATE_UNAVAILABLE"
        first = store.get_work_slot()["context"]
        retry_uid = first["unlock_mcu_command_uid"]
        retry_key = first["unlock_action_key"]

        work.handle_mcu_event(event)

        retained = store.get_work_slot()["context"]
        assert retained["phase"] == "CLEAN_RECOVERY_REQUIRED"
        assert retained["unlock_mcu_command_uid"] == retry_uid
        assert retained["unlock_action_key"] == retry_key
        assert uart.calls == calls_before_reunlock
        remote = updater.get_physical_action({"actionUid": retry_uid})
        assert remote["confirmedOutcome"] == "NOT_EXECUTED"
    finally:
        updater.close()
        store.close()


def test_clean_resume_retry_reuses_frozen_action_identity_and_token(
    tmp_path,
) -> None:
    (
        store,
        updater,
        uart,
        work,
        command,
        _start_action_uid,
        unlock_action_uid,
        _action_key,
        action_sequence,
    ) = _clean_until_unlock(tmp_path)
    work.handle_mcu_event(
        _clean_lock_event(
            command,
            mcu_command_uid=unlock_action_uid,
            action_sequence=action_sequence,
            event_sequence=2,
            power_state="DEENERGIZED",
        )
    )
    slot = store.get_work_slot()
    context = slot["context"]
    context["phase"] = "CLEAN_RECOVERY_REQUIRED"
    store.update_work_context(slot["work_uid"], context)
    calls_before_resume = list(uart.calls)
    base_client = work._job_safety._client
    client = _CommittedResponseLossClient(
        base_client,
        lose_prepare_responses=2,
        lose_get_responses=2,
    )
    work._job_safety._client = client
    resume = valid_service_command(
        "resume-clean-operation.service-wire.json"
    )
    try:
        with pytest.raises(JobSafetyError) as raised:
            work.resume_clean_command(resume)
        assert raised.value.code == "JOB_GATE_UNAVAILABLE"
        first = store.get_work_slot()["context"]
        resume_uid = first["resume_mcu_command_uid"]
        resume_key = first["resume_action_key"]
        next_sequence = first["resume_next_action_sequence"]

        result = work.resume_clean_command(resume)

        retained = store.get_work_slot()["context"]
        assert result["physicalEffect"] == "NOT_EXECUTED"
        assert retained["phase"] == "CLEAN_RECOVERY_REQUIRED"
        assert retained["resume_reconciliation_confirmed"] is False
        assert retained["resume_mcu_command_uid"] == resume_uid
        assert retained["resume_action_key"] == resume_key
        assert retained["resume_next_action_sequence"] == next_sequence
        assert uart.calls == calls_before_resume
        assert len(
            {
                request["dispatchAttemptToken"]
                for request in client.prepare_requests
            }
        ) == 1
        remote = updater.get_physical_action({"actionUid": resume_uid})
        assert remote["confirmedOutcome"] == "NOT_EXECUTED"
    finally:
        updater.close()
        store.close()


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("activeWorkType", "DELIVERY_SESSION"),
        ("activePortNo", 6),
        ("nextCleanActionSequence", 9),
    ],
)
def test_clean_boot_reconciliation_rejects_mismatched_active_facts(
    tmp_path,
    field,
    bad_value,
) -> None:
    store = make_store(tmp_path)
    manager = WorkManager(store, FakeUart(), None, FakePhotoManager())
    work_uid = "7b000000-0000-4000-8000-000000000001"
    context = {
        "phase": "RESUMING_CLEAN",
        "port_no": 2,
        "recovery_generation": 3,
        "resume_next_action_sequence": 5,
        "resume_mcu_command_uid": (
            "7b000000-0000-4000-8000-000000000002"
        ),
    }
    payload = {
        "decision": "RESUME_CLEAN_OPERATION",
        "status": "ACCEPTED",
        "mcuCommandUid": context["resume_mcu_command_uid"],
        "activeWorkType": "CLEAN_OPERATION",
        "activeWorkUid": work_uid,
        "activePortNo": 2,
        "recoveryGeneration": 3,
        "nextCleanActionSequence": 5,
        "faultCode": "NONE",
    }
    payload[field] = bad_value
    try:
        with pytest.raises(ValueError, match="does not match clean"):
            manager._on_boot_reconciliation_result(
                context,
                payload,
                work_uid,
            )
        assert context["phase"] == "RESUMING_CLEAN"
    finally:
        store.close()


def test_fixed_frame_baseline_cancelled_prepare_never_sends_query(
    tmp_path,
) -> None:
    runtime = _runtime(tmp_path)
    client = _CommittedResponseLossClient(
        runtime.client,
        lose_prepare_responses=2,
    )
    runtime.safety._client = client
    command = valid_compat_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    assert runtime.edge.receive_command(
        command["commandUid"], command["commandType"], command
    ) == "ACCEPTED"
    try:
        assert runtime.processor.process_next() is True
        inbox = runtime.edge.get_command(command["commandUid"])
        assert inbox["state"] == "FAILED"
        assert inbox["last_error"] == (
            "PHYSICAL_ACTION_PREPARE_RESPONSE_UNAVAILABLE"
        )
        assert runtime.uart.self_test_calls == []
        assert runtime.edge.get_work_slot() is None
        assert runtime.updater.get_status()["activeJobPermitCount"] == 0
    finally:
        runtime.updater.close()
        runtime.edge.close()


def test_live_pre_action_reconciler_closes_retained_prepared_token(
    tmp_path,
) -> None:
    runtime = _runtime(tmp_path)
    client = _CommittedResponseLossClient(
        runtime.client,
        lose_prepare_responses=2,
        lose_get_responses=2,
    )
    runtime.safety._client = client
    command = valid_compat_service_command(
        "measure-empty-bag-baseline.service-wire.json"
    )
    assert runtime.edge.receive_command(
        command["commandUid"], command["commandType"], command
    ) == "ACCEPTED"
    try:
        assert runtime.processor.process_next() is True
        assert runtime.edge.get_command(command["commandUid"])["state"] == (
            "RECOVERY_REQUIRED"
        )
        assert runtime.uart.self_test_calls == []

        assert runtime.work.reconcile_pre_action_job_safety_failure()

        inbox = runtime.edge.get_command(command["commandUid"])
        assert inbox["state"] == "FAILED"
        assert runtime.uart.self_test_calls == []
        assert runtime.edge.get_work_slot() is None
        assert runtime.updater.get_status()["activeJobPermitCount"] == 0
        action_uid = command["payload"]["measurementUid"]
        remote = runtime.updater.get_physical_action(
            {"actionUid": action_uid}
        )
        assert remote["confirmedOutcome"] == "NOT_EXECUTED"
    finally:
        runtime.updater.close()
        runtime.edge.close()


def _clean_until_unlock(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    uart = FakeUart()
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    command = valid_service_command(
        "start-clean-operation.service-wire.json"
    )
    assert store.receive_command(
        command["commandUid"], command["commandType"], command
    ) == "ACCEPTED"
    assert work.start_clean_command(command)["acked"] is True
    start_action_uid = uart.calls[-1][2]
    work.handle_mcu_event(
        clean_preunlock_event(
            command,
            start_action_uid,
            str(uuid.uuid4()),
        )
    )
    assert uart.calls[-1][0] == "UNLOCK_CLEAN_DOOR"
    unlock_action_uid = uart.calls[-1][2]
    slot = store.get_work_slot()
    assert slot is not None
    action_sequence = slot["context"]["action_sequence"]
    action_key = (
        "CLEAN:UNLOCK:"
        f"{slot['context']['recovery_generation']}:{action_sequence}"
    )
    return (
        store,
        updater,
        uart,
        work,
        command,
        start_action_uid,
        unlock_action_uid,
        action_key,
        action_sequence,
    )


def _clean_lock_event(
    command: dict,
    *,
    mcu_command_uid: str,
    action_sequence: int,
    event_sequence: int,
    power_state: str,
) -> dict:
    return {
        "message_name": "CLEAN_LOCK_POWER_CHANGED",
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": event_sequence,
            "uptimeMs": event_sequence * 1_000,
            "mcuCommandUid": mcu_command_uid,
            "operationUid": command["payload"]["operationUid"],
            "portNo": command["payload"]["portNo"],
            "cleanActionSequence": action_sequence,
            "lockPowerState": power_state,
            "solenoidHealth": "OK",
            "faultCode": "NONE",
        },
    }


def test_clean_unlock_accepts_energized_then_deenergized_with_one_action_uid(
    tmp_path,
) -> None:
    (
        store,
        updater,
        _uart,
        work,
        command,
        _start_action_uid,
        unlock_action_uid,
        action_key,
        action_sequence,
    ) = _clean_until_unlock(tmp_path)
    try:
        work.handle_mcu_event(
            _clean_lock_event(
                command,
                mcu_command_uid=unlock_action_uid,
                action_sequence=action_sequence,
                event_sequence=2,
                power_state="ENERGIZED",
            )
        )
        first_remote = updater.get_physical_action(
            {"actionUid": unlock_action_uid}
        )
        first_local = copy.deepcopy(
            store.get_work_slot()["context"]["job_safety"][
                "confirmations"
            ][action_key]
        )
        assert first_remote["state"] == "CONFIRMED"
        assert first_remote["confirmedOutcome"] == "EXECUTED"
        assert first_remote["confirmationBasis"] == (
            "MCU_IDENTITY_BOUND_FACT"
        )

        work.handle_mcu_event(
            _clean_lock_event(
                command,
                mcu_command_uid=unlock_action_uid,
                action_sequence=action_sequence,
                event_sequence=3,
                power_state="DEENERGIZED",
            )
        )

        slot = store.get_work_slot()
        assert slot["context"]["phase"] == "ACTIVE"
        assert slot["context"]["clean_lock_power_state"] == "DEENERGIZED"
        second_remote = updater.get_physical_action(
            {"actionUid": unlock_action_uid}
        )
        assert second_remote["receiptUid"] == first_remote["receiptUid"]
        assert second_remote["confirmedOutcome"] == (
            first_remote["confirmedOutcome"]
        )
        assert second_remote["evidenceDigestSha256"] == (
            first_remote["evidenceDigestSha256"]
        )
        assert slot["context"]["job_safety"]["confirmations"][
            action_key
        ] == first_local
    finally:
        updater.close()
        store.close()


@pytest.mark.parametrize("corruption", ["action_key", "action_kind"])
def test_clean_lock_fact_rejects_wrong_expected_action_record(
    tmp_path,
    corruption,
) -> None:
    (
        store,
        updater,
        _uart,
        work,
        command,
        _start_action_uid,
        unlock_action_uid,
        action_key,
        action_sequence,
    ) = _clean_until_unlock(tmp_path)
    try:
        slot = store.get_work_slot()
        context = slot["context"]
        actions = context["job_safety"]["actions"]
        if corruption == "action_key":
            actions[f"{action_key}:WRONG"] = actions.pop(action_key)
        else:
            actions[action_key]["action_kind"] = "START_CLEAN_OPERATION"
        store.update_work_context(slot["work_uid"], context)

        with pytest.raises(JobSafetyError) as raised:
            work.handle_mcu_event(
                _clean_lock_event(
                    command,
                    mcu_command_uid=unlock_action_uid,
                    action_sequence=action_sequence,
                    event_sequence=2,
                    power_state="ENERGIZED",
                )
            )

        assert raised.value.code == "PHYSICAL_ACTION_EVIDENCE_MISMATCH"
        remote = updater.get_physical_action(
            {"actionUid": unlock_action_uid}
        )
        assert remote["state"] == "ARMED"
        assert remote["receiptUid"] is None
        assert store.get_work_slot()["context"]["phase"] == (
            "WAITING_LOCK_OUTPUT"
        )
    finally:
        updater.close()
        store.close()


def test_clean_lock_fact_cannot_use_start_uid_as_unlock_evidence(
    tmp_path,
) -> None:
    (
        store,
        updater,
        _uart,
        work,
        command,
        start_action_uid,
        unlock_action_uid,
        _action_key,
        action_sequence,
    ) = _clean_until_unlock(tmp_path)
    try:
        with pytest.raises(ValueError, match="does not match active clean"):
            work.handle_mcu_event(
                _clean_lock_event(
                    command,
                    mcu_command_uid=start_action_uid,
                    action_sequence=action_sequence,
                    event_sequence=2,
                    power_state="ENERGIZED",
                )
            )

        unlock_remote = updater.get_physical_action(
            {"actionUid": unlock_action_uid}
        )
        assert unlock_remote["state"] == "ARMED"
        assert unlock_remote["receiptUid"] is None
        slot = store.get_work_slot()
        assert slot["context"]["phase"] == "WAITING_LOCK_OUTPUT"
        assert slot["context"].get("clean_lock_power_state") is None
    finally:
        updater.close()
        store.close()
