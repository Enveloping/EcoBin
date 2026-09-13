"""Real clean candidate -> explicit human confirmation -> immutable final custody."""
import uuid

import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, original_scope, take_samples
from hardware.tests.test_mcu_clean_intent import active_clean, request, save_intent
from hardware.tests.test_mcu_clean_execution import advance
from hardware.tests.test_mcu_delivery_execution import facts

NAME = "CLEAN_COMPLETION_CONFIRMED"


def measured_candidate(runtime, tmp_path, *, available=True, saved_final=True, interrupted=False):
    clean, start, initial, _, now = active_clean(runtime, tmp_path)
    store = EdgeStore(str(tmp_path / "candidate.db"))
    store.initialize()
    initial_scope = original_scope(start, clean=True) | {"eventMessageType": "WORK_PREUNLOCK_WEIGHT_READY",
        "stepSequence": 0, "configVersion": start["configVersion"]}
    store.save_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", initial_scope)[8:],
        "WORK_PREUNLOCK_WEIGHT_READY", uart.encode_payload("WORK_PREUNLOCK_WEIGHT_READY", initial))
    assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
    save_intent(runtime, start, "CLEAN_FINISH_REQUESTED", 1, now, store)
    now = advance(runtime, now, 0)
    if interrupted:
        now = take_samples(runtime, [123], start=now, measurement=2)
        lib, _, preparation, *_ = runtime
        assert lib.McuWeightRun_Interrupt(lib.TestPreparation_Weight(preparation), 2, now)
        now = advance(runtime, now, 0)
    else:
        now = take_samples(runtime, [123] * 5, start=now, measurement=2) if available else advance(runtime, now, 5000)
    current = facts(runtime, now)
    assert current["scaleReadStatus"] == "VALID"
    assert current["scaleAttemptSequence"] == (6 if interrupted else 10 if available else 5)
    assert current["scaleCapturedUptimeMs"] == (now if available or interrupted else initial["uptimeMs"])
    assert current["scaleWeightGrams"] == (123 if available or interrupted else initial["reportedWeightGrams"])
    if saved_final:
        final = save_intent(runtime, start, "CLEAN_FINAL_WEIGHT_READY", 1, now, store)
    else:
        scope = original_scope(start, clean=True) | {"eventMessageType": "CLEAN_FINAL_WEIGHT_READY", "stepSequence": 1,
            "configVersion": start["configVersion"]}
        final = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    return clean, start, initial, final, now, store


def confirm(runtime, clean, start, final, now, *, sequence=1):
    lib, endpoint, *_ = runtime
    function = getattr(lib, "McuCleanExecution_Confirm", None)
    assert function is not None, "native clean human-confirmation boundary is not implemented"
    return function(clean, endpoint, uuid.UUID(start["operationUid"]).bytes, sequence,
        uuid.UUID(final["measurementUid"]).bytes, now)


def test_only_explicit_current_candidate_confirmation_retains_a_human_fact_and_waits_for_save(runtime, tmp_path):
    clean, start, initial, final, now, store = measured_candidate(runtime, tmp_path)
    try:
        runtime[0].TestFacts_Pinch(1)  # PB5 pause is not manual clean-door evidence or a fault.
        now = advance(runtime, now, 100)
        assert exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=now)[0][1]["phase"] == "CLEAN_RESULT_CONFIRMATION"
        assert confirm(runtime, clean, start, final, now)
        scope = original_scope(start, clean=True) | {"eventMessageType": NAME, "stepSequence": 1, "configVersion": start["configVersion"]}
        query, record = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)
        assert query[1]["status"] == "HELD"
        fact = record[1]
        assert fact["mcuCommandUid"] == start["mcuCommandUid"]
        assert fact["finalMeasurementUid"] == final["measurementUid"] != initial["measurementUid"]
        assert fact["cleanActionSequence"] == 1 and fact["uptimeMs"] == now
        assert fact["cleanerPhysicalCloseConfirmed"] and fact["cleanDoorStateBasis"] == "CLEANER_CONFIRMATION"
        assert fact["lockPowerState"] == "DEENERGIZED" and fact["solenoidHealth"] == "UNKNOWN"
        assert not confirm(runtime, clean, start, final, now)
        now = advance(runtime, now, 100)
        state = exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=now)[0][1]
        assert state["status"] == "RUNNING" and state["phase"] == "CLEAN_FINALIZING"
        assert not facts(runtime, now)["cleanLockPowered"] and store.list_native_result_report_tasks() == []
        assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1] == fact
    finally:
        store.close()


def held_result(runtime, start, now):
    observed = exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=now)[0][1]
    assert observed["status"] == "RESULT_HELD"
    identity = dict(mcuBootId=42, resultSequence=observed["resultSequence"], workUid=start["operationUid"],
        resultDigestSha256=observed["resultDigestSha256"])
    replies = exchange(runtime, "QUERY_RESULT", identity | {"queryId": 123}, now=now)
    assert replies[0][1]["status"] == "HELD"
    return replies[1][1]


@pytest.mark.parametrize("available", [True, False])
def test_saved_human_confirmation_assembles_original_clean_result_without_remeasurement_or_pi_release(runtime, tmp_path, available):
    clean, start, initial, final, now, store = measured_candidate(runtime, tmp_path, available=available)
    try:
        assert store.acquire_work_slot("CLEAN", start["operationUid"], 1, {"phase": "CLEAN_FINALIZING"})
        occupied = store.get_work_slot()
        assert confirm(runtime, clean, start, final, now)
        confirmation = save_intent(runtime, start, NAME, 1, now, store)
        now = advance(runtime, now, 123)
        result = held_result(runtime, start, now)
        assert result["workUid"] == start["operationUid"] and result["workType"] == "CLEAN_OPERATION"
        assert result["originCommandUid"] == start["mcuCommandUid"] and result["originCommandSequence"] == start["commandSequence"]
        assert result["configVersion"] == start["configVersion"] and result["completedUptimeMs"] == confirmation["uptimeMs"]
        assert result["cleanActionSequence"] == 1 and result["finishReason"] == "CLEAN_CONFIRMED"
        assert result["physicalCloseConfirmed"] and not result["negativeWeightAnomaly"] and result["deliveryRoundCount"] == 0
        for prefix, weight in (("initial", initial), ("final", final)):
            assert result[prefix + "MeasurementUid"] == weight["measurementUid"]
            assert result[prefix + "McuEventSequence"] == weight["mcuEventSequence"]
            assert result[prefix + "WeightGrams"] == weight["reportedWeightGrams"]
            assert result[prefix + "Kind"] == weight["measurementKind"]
            assert result[prefix + "FaultCode"] == weight["faultCode"]
        receipt = store.save_native_mcu_result(uart.encode_payload("WORK_RESULT", result))
        assert exchange(runtime, "RESULT_SAVED", payload=receipt["savedPayload"], now=now)[0][1]["status"] == "RELEASED"
        now = advance(runtime, now, 1000)
        assert facts(runtime, now)["measurementSequence"] == 2 and not facts(runtime, now)["cleanLockPowered"]
        assert store.get_work_slot() == occupied and len(store.list_native_result_report_tasks()) == 1
        assert not confirm(runtime, clean, start, final, now)
    finally:
        store.close()


@pytest.mark.parametrize("case", ["initial_weight", "other_weight", "other_operation", "old_step", "unsaved_final",
    "reopened", "expired", "update", "guard_denied", "empty_slot", "event_exhausted", "guard_update", "guard_expiry"])
def test_confirmation_rejects_stale_candidate_or_changed_context_without_completing(runtime, tmp_path, case):
    clean, start, initial, final, now, store = measured_candidate(runtime, tmp_path, saved_final=case != "unsaved_final")
    lib, endpoint, *_ = runtime
    sequence = 1
    try:
        if case == "initial_weight":
            final = initial
        elif case == "other_weight":
            final = final | {"measurementUid": "99999999-9999-4999-8999-999999999999"}
        elif case == "other_operation":
            start = start | {"operationUid": "99999999-9999-4999-8999-999999999999"}
        elif case == "old_step":
            sequence = 0
        elif case == "reopened":
            assert request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now, after=1)
        elif case == "expired":
            now = advance(runtime, now, start["operationWindowMs"])
        elif case == "update":
            lib.ActuatorRuntime_StopForUpdate()
        elif case == "guard_denied":
            runtime[4]["error"] = 11
        elif case == "empty_slot":
            lib.McuProcessEventSlot_Init(lib.TestPreparation_Process(endpoint), 42)
        elif case == "event_exhausted":
            lib.TestPreparation_SetEventSequence(endpoint, 2**32 - 1)
        elif case == "guard_update":
            runtime[4]["inspect"] = lambda *_: lib.ActuatorRuntime_StopForUpdate()
        elif case == "guard_expiry":
            runtime[4]["inspect"] = lambda *_: advance(runtime, now, start["operationWindowMs"], poll=False)
        assert not confirm(runtime, clean, start, final, now, sequence=sequence)
        if case == "guard_expiry":
            now += start["operationWindowMs"]
        assert store.list_native_result_report_tasks() == []
        assert not facts(runtime, now)["cleanLockPowered"]
        assert facts(runtime, now)["measurementSequence"] == 2
    finally:
        store.close()


def test_interrupted_candidate_keeps_human_close_fact_but_never_becomes_normal_completion(runtime, tmp_path):
    # Explicit acquisition interruption boundary; automatic clean interruption
    # detection is a separate workflow, not simulated by mutating owner fields.
    clean, start, _, final, now, store = measured_candidate(runtime, tmp_path, interrupted=True)
    try:
        assert final["measurementKind"] == "INTERRUPTED" and final["sampleCount"] == 1
        assert confirm(runtime, clean, start, final, now)
        save_intent(runtime, start, NAME, 1, now, store)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "FAILED" and result["physicalCloseConfirmed"]
        assert result["finalKind"] == "INTERRUPTED" and result["finalFaultCode"] == "MEASUREMENT_INTERRUPTED"
        assert result["finalMeasurementUid"] == final["measurementUid"] and result["finalSampleCount"] == 1
        assert result["completedUptimeMs"] == now and result["cleanActionSequence"] == 1
        assert facts(runtime, now)["measurementSequence"] == 2 and not facts(runtime, now)["cleanLockPowered"]
    finally:
        store.close()


@pytest.mark.parametrize("available", [True, False])
@pytest.mark.parametrize("stage,loss", [(stage, loss) for stage in ("process", "result")
    for loss in ("exception", "short", "lost", "reply_lost")])
def test_real_pi_commits_confirmation_and_result_before_receipts_and_resumes_after_restart(runtime, tmp_path, available, stage, loss):
    from mcu_process_handoff import McuProcessEventHandoff
    from mcu_result_handoff import McuResultHandoff
    clean, start, _, final, now, store = measured_candidate(runtime, tmp_path, available=available)
    lib, endpoint, preparation, replies, *_ = runtime
    path = str(tmp_path / "candidate.db")
    assert store.acquire_work_slot("CLEAN", start["operationUid"], 1, {"phase": "CLEAN_FINALIZING"})
    occupied = store.get_work_slot()
    assert confirm(runtime, clean, start, final, now)
    scope = original_scope(start, clean=True) | {"eventMessageType": NAME, "stepSequence": 1, "configVersion": start["configVersion"]}
    raw_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
    original = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    raw = uart.encode_payload(NAME, original)
    broken, sent, final_raw = True, [], None
    lost_message = "PROCESS_EVENT_SAVED" if stage == "process" else "RESULT_SAVED"

    def write(frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        name = decoded["messageName"]
        sent.append(name)
        if name in {"PROCESS_EVENT_SAVED", "RESULT_SAVED"}:
            reader = EdgeStore(path)
            reader.initialize()
            try:
                assert reader.get_native_process_receipt(raw_scope)["payload"] == raw
                assert reader.get_work_slot() == occupied
                if name == "PROCESS_EVENT_SAVED":
                    assert reader.get_native_process_receipt(raw_scope)["saved_payload"] == decoded["payload"]
                    assert reader.list_native_result_report_tasks() == []
                else:
                    assert reader.get_native_mcu_result(42, 1)["payload"] == final_raw
                    assert len(reader.list_native_result_report_tasks()) == 1
            finally:
                reader.close()
            if broken and name == lost_message:
                if loss == "exception":
                    raise OSError("synthetic clean confirmation transfer failure")
                if loss == "short":
                    return 1
                if loss == "lost":
                    return len(frame)
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        if broken and name == lost_message and loss == "reply_lost":
            replies.clear()
        return len(frame)

    def pump(client):
        while replies:
            client.accept_frame(replies.pop(0), now)

    try:
        replies.clear()
        identity = {key: value for key, value in scope.items() if key != "queryId"}
        client = McuProcessEventHandoff(store, write, identity)
        first_query = client.poll(now)
        pump(client)
        if stage == "process":
            assert client.last_write_error == {"exception": "WRITE_FAILED", "short": "SHORT_WRITE"}.get(loss)
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = stage == "result"
        client = McuProcessEventHandoff(store, write, identity)
        now = advance(runtime, now, 1000, poll=False)
        assert client.poll(now) > first_query
        pump(client)
        now = advance(runtime, now, 1000, poll=False)
        client.poll(now)
        pump(client)
        assert client.observation(now)["status"] == "RELEASED"
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        result = held_result(runtime, start, now)
        assert result["completedUptimeMs"] == original["uptimeMs"] and result["physicalCloseConfirmed"]
        assert result["finalMeasurementUid"] == final["measurementUid"]
        final_raw = uart.encode_payload("WORK_RESULT", result)
        result_identity = uart.decode_payload("RESULT_SAVED", final_raw[:60])
        replies.clear()
        client = McuResultHandoff(store, write, result_identity)
        first_query = client.poll(now)
        pump(client)
        if stage == "result":
            assert client.last_write_error == {"exception": "WRITE_FAILED", "short": "SHORT_WRITE"}.get(loss)
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        client = McuResultHandoff(store, write, result_identity)
        now = advance(runtime, now, 1000)
        assert client.poll(now) > first_query
        pump(client)
        now = advance(runtime, now, 1000)
        client.poll(now)
        pump(client)
        assert client.query_observation(now)["status"] == "RELEASED"
        assert store.get_native_process_receipt(raw_scope)["payload"] == raw
        assert store.get_native_mcu_result(42, 1)["payload"] == final_raw
        assert store.get_work_slot() == occupied
        assert [row["state"] for row in store.list_native_result_report_tasks()] == ["PENDING_CLASSIFICATION"]
        assert set(sent) == {"QUERY_PROCESS_EVENT", "PROCESS_EVENT_SAVED", "QUERY_RESULT", "RESULT_SAVED"}
        assert facts(runtime, now)["measurementSequence"] == 2 and not facts(runtime, now)["cleanLockPowered"]
        assert not lib.McuWeightRun_StartOwnedAttempt(lib.TestPreparation_Weight(preparation), now)
    finally:
        store.close()


def test_database_commit_failure_keeps_human_fact_held_and_never_sends_saved(runtime, tmp_path):
    import sqlite3
    from mcu_process_handoff import McuProcessEventHandoff
    clean, start, _, final, now, store = measured_candidate(runtime, tmp_path)
    lib, endpoint, _, replies, *_ = runtime
    assert confirm(runtime, clean, start, final, now)
    identity = {key: value for key, value in original_scope(start, clean=True).items() if key != "queryId"} | {
        "eventMessageType": NAME, "stepSequence": 1, "configVersion": start["configVersion"]}
    sent = []

    def write(frame):
        sent.append(uart.decode_frame(frame, sender_role="EDGE")["messageName"])
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        return len(frame)

    try:
        replies.clear()
        client = McuProcessEventHandoff(store, write, identity)
        client.poll(now)
        assert client.accept_frame(replies.pop(0), now)
        body = replies.pop(0)
        store._conn.set_authorizer(lambda action, first, *_:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            client.accept_frame(body, now)
        store._conn.set_authorizer(None)
        assert sent == ["QUERY_PROCESS_EVENT"]
        now = advance(runtime, now, 1000)
        assert exchange(runtime, "QUERY_PROCESS_EVENT", identity | {"queryId": 1}, now=now)[0][1]["status"] == "HELD"
        assert exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=now)[0][1]["phase"] == "CLEAN_FINALIZING"
        assert store.get_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", identity | {"queryId": 1})[8:]) is None
        assert store.list_native_result_report_tasks() == [] and not facts(runtime, now)["cleanLockPowered"]
    finally:
        store._conn.set_authorizer(None)
        store.close()


def test_confirmation_already_frozen_is_not_rewritten_by_later_update_or_expiry(runtime, tmp_path):
    clean, start, _, final, now, store = measured_candidate(runtime, tmp_path)
    try:
        assert confirm(runtime, clean, start, final, now)
        confirmed_at = now
        runtime[0].ActuatorRuntime_StopForUpdate()
        now = advance(runtime, now, start["operationWindowMs"])
        confirmation = save_intent(runtime, start, NAME, 1, now, store)
        assert confirmation["uptimeMs"] == confirmed_at
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["completedUptimeMs"] == confirmed_at and result["physicalCloseConfirmed"]
        assert result["finishReason"] == "CLEAN_CONFIRMED" and result["finalMeasurementUid"] == final["measurementUid"]
        assert facts(runtime, now)["updateLatched"] and not facts(runtime, now)["cleanLockPowered"]
        assert store.list_native_result_report_tasks() == []  # MCU result has not yet been saved to Pi.
    finally:
        store.close()


@pytest.mark.parametrize("saved_confirmation", [True, False])
def test_actual_mcu_reset_cannot_replay_old_confirmation_or_claim_result_custody(runtime, tmp_path, saved_confirmation):
    clean, start, _, final, now, store = measured_candidate(runtime, tmp_path)
    lib, endpoint, preparation, _, _, sink, guard = runtime
    try:
        assert store.acquire_work_slot("CLEAN", start["operationUid"], 1, {"phase": "CLEAN_FINALIZING"})
        occupied = store.get_work_slot()
        assert confirm(runtime, clean, start, final, now)
        if saved_confirmation:
            save_intent(runtime, start, NAME, 1, now, store)
        # Reinitialize actual C owners/hardware at the explicit MCU reset edge.
        # This is never the Pi-only reconnect path used above.
        lib.TestFacts_InitHardware()
        lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
        assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
        assert lib.McuCleanExecution_Attach(clean, preparation, endpoint)
        assert exchange(runtime, "BOOT_PROBE", {"probeId": 2})[0][1]["mcuBootId"] == 0
        assert exchange(runtime, "BIND_BOOT", {"probeId": 2, "proposedMcuBootId": 43})[0][1]["status"] == "BOUND"
        assert not confirm(runtime, clean, start, final, 0)
        assert exchange(runtime, "QUERY_WORK", original_scope(start, clean=True))[0][1]["status"] == "BOOT_MISMATCH"
        scope = original_scope(start, clean=True) | {"eventMessageType": NAME, "stepSequence": 1, "configVersion": start["configVersion"]}
        assert exchange(runtime, "QUERY_PROCESS_EVENT", scope)[0][1]["status"] == "BOOT_MISMATCH"
        row = store.get_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:])
        assert (row is not None) == saved_confirmation
        assert store.get_work_slot() == occupied and store.list_native_result_report_tasks() == []
        # Outcome classification/recovery remains a later Pi responsibility.
    finally:
        store.close()


def test_reopened_clean_confirms_only_new_candidate_and_keeps_old_weight_for_diagnosis(runtime, tmp_path):
    from hardware.tests.test_mcu_opening_gate import grant
    from hardware.tests.test_mcu_delivery_execution import events
    clean, start, initial, old, now, store = measured_candidate(runtime, tmp_path)
    try:
        assert request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now, after=1)
        save_intent(runtime, start, "CLEAN_UNLOCK_REQUESTED", 2, now, store)
        name, command = grant(start, initial, clean=True, commandSequence=8, cleanActionSequence=2,
            mcuCommandUid="88888888-8888-4888-8888-888888888888")
        assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
        now = advance(runtime, now, command["unlockPulseMs"])
        for _ in range(2):
            event = events(runtime, now)[1][1]
            receipt = store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", uart.encode_payload("CLEAN_LOCK_POWER_CHANGED", event))
            assert exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now, after=2)
        save_intent(runtime, start, "CLEAN_FINISH_REQUESTED", 3, now, store)
        now = advance(runtime, now, 0)
        now = take_samples(runtime, [456] * 5, start=now, measurement=3)
        new = save_intent(runtime, start, "CLEAN_FINAL_WEIGHT_READY", 3, now, store)
        assert not confirm(runtime, clean, start, old, now, sequence=1)
        assert not confirm(runtime, clean, start, old, now, sequence=3)
        assert confirm(runtime, clean, start, new, now, sequence=3)
        save_intent(runtime, start, NAME, 3, now, store)
        now = advance(runtime, now, 0)
        result = held_result(runtime, start, now)
        assert result["cleanActionSequence"] == 3 and result["finalMeasurementUid"] == new["measurementUid"]
        assert result["finalWeightGrams"] == 456 and result["initialMeasurementUid"] == initial["measurementUid"]
        assert store.get_native_process_event("CLEAN_FINAL_WEIGHT_READY", 42, old["mcuEventSequence"])["payload"] == uart.encode_payload("CLEAN_FINAL_WEIGHT_READY", old)
        assert facts(runtime, now)["measurementSequence"] == 3 and not facts(runtime, now)["cleanLockPowered"]
    finally:
        store.close()
