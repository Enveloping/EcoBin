"""Real clean execution -> original button intent -> Pi exact custody."""
import uuid

import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, original_scope, take_samples
from hardware.tests.test_mcu_opening_gate import prepared, grant
from hardware.tests.test_mcu_clean_execution import enable, advance
from hardware.tests.test_mcu_delivery_execution import events, facts


def active_clean(runtime, tmp_path, *, saved_edges=2):
    clean = enable(runtime)
    lib, *_ = runtime
    assert lib.ActuatorRuntime_SetDoorTarget(1)
    start, initial, now = prepared(runtime, tmp_path, clean=True)
    name, command = grant(start, initial, clean=True)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    now = advance(runtime, now, command["unlockPulseMs"])
    store = EdgeStore(str(tmp_path / "initial-edges.db"))
    store.initialize()
    try:
        for _ in range(saved_edges):
            message, event = events(runtime, now)[1]
            receipt = store.save_native_actuator_event(message, uart.encode_payload(message, event))
            assert exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
    finally:
        store.close()
    return clean, start, initial, command, now


def request(runtime, clean, start, message, now, after=0):
    lib, endpoint, *_ = runtime
    return lib.McuCleanExecution_Request(clean, endpoint, uuid.UUID(start["operationUid"]).bytes,
        uart.MESSAGE_SPECS[message]["id"], after, now)


def save_intent(runtime, start, message, sequence, now, store):
    scope = original_scope(start, clean=True) | {"eventMessageType": message, "stepSequence": sequence, "configVersion": start["configVersion"]}
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    receipt = store.save_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:], message, uart.encode_payload(message, event))
    assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
    return event


@pytest.mark.parametrize("message", ["CLEAN_UNLOCK_REQUESTED", "CLEAN_FINISH_REQUESTED"])
def test_clean_intent_is_retained_and_saved_under_original_work_without_motion_or_completion(runtime, tmp_path, message):
    clean, start, initial, command, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, message, now)
    scope = original_scope(start, clean=True) | {"eventMessageType": message, "stepSequence": 1, "configVersion": start["configVersion"]}
    frames = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)
    assert frames[0][1]["status"] == "HELD"
    event = frames[1][1]
    assert event["mcuCommandUid"] == start["mcuCommandUid"] != command["mcuCommandUid"]
    assert event["operationUid"] == start["operationUid"] and event["cleanActionSequence"] == 1
    assert event["configVersion"] == start["configVersion"] and event["uptimeMs"] == now
    assert not request(runtime, clean, start, message, now)
    assert not facts(runtime, now)["cleanLockPowered"]
    path = str(tmp_path / "intent.db")
    store = EdgeStore(path)
    store.initialize()
    try:
        assert store.acquire_work_slot("CLEAN", start["operationUid"], 1, {"phase": "CLEAN_ACTIVE"})
        occupancy = store.get_work_slot()
        raw_scope, raw = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:], uart.encode_payload(message, event)
        receipt = store.save_native_process_receipt(raw_scope, message, raw)
        assert store.get_native_process_receipt(raw_scope)["payload"] == raw
        assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        assert store.get_native_process_event(message, 42, event["mcuEventSequence"])["payload"] == raw
        assert store.get_work_slot() == occupancy and store.list_native_result_report_tasks() == []
        assert store.get_native_measurement_event(42, event["mcuEventSequence"]) is None
        assert not facts(runtime, now)["cleanLockPowered"] and initial
    finally:
        store.close()


def test_local_intent_guard_receives_the_actual_valid_candidate_not_a_zero_event_identity(runtime, tmp_path):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    seen = []
    runtime[4]["inspect"] = lambda name, raw, at: seen.append((name, raw, at))
    assert request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now)
    assert len(seen) == 1
    assert uart.decode_payload("CLEAN_UNLOCK_REQUESTED", seen[0][1])["mcuEventSequence"] > 0


@pytest.mark.parametrize("change", ["update", "expiry"])
def test_intent_rechecks_runtime_after_application_guard_before_accepting(runtime, tmp_path, change):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    lib, *_ = runtime
    runtime[4]["inspect"] = lambda *_: (lib.ActuatorRuntime_StopForUpdate() if change == "update"
        else advance(runtime, now, start["operationWindowMs"], poll=False))
    assert not request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now)
    scope = original_scope(start, clean=True) | {"eventMessageType": "CLEAN_UNLOCK_REQUESTED", "stepSequence": 1,
        "configVersion": start["configVersion"]}
    at = now if change == "update" else now + start["operationWindowMs"]
    assert exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=at)[0][1]["status"] == "NOT_FOUND"
    assert not facts(runtime, at)["cleanLockPowered"]


@pytest.mark.parametrize("case", ["on_unsaved", "off_unsaved", "stale_action", "other_operation", "guard_denied", "update", "expired", "event_exhausted"])
def test_local_button_requires_original_context_and_saved_power_edges(runtime, tmp_path, case):
    clean, start, _, _, now = active_clean(runtime, tmp_path, saved_edges={"on_unsaved": 0, "off_unsaved": 1}.get(case, 2))
    lib, endpoint, *_ = runtime
    if case == "other_operation":
        start = start | {"operationUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
    elif case == "guard_denied":
        runtime[4]["error"] = 11
    elif case == "update":
        lib.ActuatorRuntime_StopForUpdate()
    elif case == "expired":
        now = advance(runtime, now, start["operationWindowMs"])
    elif case == "event_exhausted":
        lib.TestPreparation_SetEventSequence(endpoint, 2**32 - 1)
    assert not request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now, after=1 if case == "stale_action" else 0)
    assert not facts(runtime, now)["cleanLockPowered"]
    assert facts(runtime, now)["measurementSequence"] == 1


@pytest.mark.parametrize("message,saved,changes,expected", [
    ("CLEAN_UNLOCK_REQUESTED", False, {}, "BUSY"),
    ("CLEAN_FINISH_REQUESTED", True, {}, "STATE_CONFLICT"),
    ("CLEAN_UNLOCK_REQUESTED", True, {"cleanActionSequence": 2}, "STATE_CONFLICT"),
    ("CLEAN_UNLOCK_REQUESTED", True, {"recoveryGeneration": 1}, "STATE_CONFLICT"),
    ("CLEAN_UNLOCK_REQUESTED", True, {"parentCommandUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}, "UNKNOWN_WORK"),
    ("CLEAN_UNLOCK_REQUESTED", True, {"operationUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}, "UNKNOWN_WORK"),
    ("CLEAN_UNLOCK_REQUESTED", True, {"unlockPulseMs": 1001}, "STATE_CONFLICT"),
])
def test_reopen_grant_requires_exact_saved_unlock_intent_and_original_parameters(runtime, tmp_path, message, saved, changes, expected):
    clean, start, initial, _, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, message, now)
    store = EdgeStore(str(tmp_path / "bad-grant.db"))
    store.initialize()
    try:
        if saved:
            save_intent(runtime, start, message, 1, now, store)
        name, command = grant(start, initial, clean=True, **(dict(commandSequence=8, cleanActionSequence=1,
            mcuCommandUid="88888888-8888-4888-8888-888888888888") | changes))
        decision = exchange(runtime, name, command, now=now)[0][1]
        assert decision["outcome"] == "REJECTED" and decision["errorCode"] == expected
        assert exchange(runtime, name, command, now=now)[0][1] == decision
        assert not facts(runtime, now)["cleanLockPowered"]
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


def test_saved_reopen_grant_cannot_extend_the_first_grants_remaining_operation_window(runtime, tmp_path):
    clean, start, initial, first, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now)
    store = EdgeStore(str(tmp_path / "expired-grant.db"))
    store.initialize()
    try:
        save_intent(runtime, start, "CLEAN_UNLOCK_REQUESTED", 1, now, store)
        deadline = initial["uptimeMs"] + first["remainingOperationWindowMs"]
        now = advance(runtime, now, deadline - now)
        name, command = grant(start, initial, clean=True, commandSequence=8, cleanActionSequence=1,
            mcuCommandUid="88888888-8888-4888-8888-888888888888", remainingOperationWindowMs=200000)
        decision = exchange(runtime, name, command, now=now)[0][1]
        assert decision["outcome"] == "REJECTED" and decision["errorCode"] == "EXPIRED"
        assert not facts(runtime, now)["cleanLockPowered"]
    finally:
        store.close()


def test_saved_reopen_request_allows_one_new_pulse_and_then_a_new_finish_generation(runtime, tmp_path):
    clean, start, initial, first, now = active_clean(runtime, tmp_path)
    runtime[0].TestFacts_Pinch(1)  # PB5 CLOSE pause alone does not prohibit clean unlock.
    now = advance(runtime, now, 40000 - now)  # First START execution window has ended; operation remains active.
    assert request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now)
    store = EdgeStore(str(tmp_path / "reopen.db"))
    store.initialize()
    try:
        save_intent(runtime, start, "CLEAN_UNLOCK_REQUESTED", 1, now, store)
        name, command = grant(start, initial, clean=True, commandSequence=8, cleanActionSequence=1,
            mcuCommandUid="88888888-8888-4888-8888-888888888888")
        decision = exchange(runtime, name, command, now=now)[0][1]
        assert decision["outcome"] == "ACCEPTED"
        assert facts(runtime, now)["cleanLockPowered"]
        assert exchange(runtime, name, command, now=now)[0][1] == decision
        now = advance(runtime, now, command["unlockPulseMs"])
        for state in ("ENERGIZED", "DEENERGIZED"):
            event = events(runtime, now)[1][1]
            assert event["mcuCommandUid"] == command["mcuCommandUid"] != first["mcuCommandUid"]
            assert event["lockPowerState"] == state
            receipt = store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", uart.encode_payload("CLEAN_LOCK_POWER_CHANGED", event))
            assert exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        assert not request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now, after=0)
        assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now, after=1)
        finish = save_intent(runtime, start, "CLEAN_FINISH_REQUESTED", 2, now, store)
        assert finish["cleanActionSequence"] == 2 and not facts(runtime, now)["cleanLockPowered"]
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["stable", "unavailable", "median"])
def test_finish_intent_requires_exact_save_then_takes_new_final_weight_without_completing(runtime, tmp_path, mode):
    clean, start, initial, _, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
    now = advance(runtime, now, 100)
    assert facts(runtime, now)["measurementSequence"] == 1
    store = EdgeStore(str(tmp_path / "finish.db"))
    store.initialize()
    try:
        save_intent(runtime, start, "CLEAN_FINISH_REQUESTED", 1, now, store)
        now = advance(runtime, now, 0)
        assert exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=now)[0][1]["phase"] == "CLEAN_FINAL_MEASURING"
        if mode == "stable":
            now = take_samples(runtime, [123] * 5, start=now, measurement=2)
        elif mode == "median":
            started = now
            now = take_samples(runtime, [100, 500] * 10, start=now, measurement=2)
            now = advance(runtime, now, started + 5000 - now)
        else:
            now = advance(runtime, now, 5000)
        scope = original_scope(start, clean=True) | {"eventMessageType": "CLEAN_FINAL_WEIGHT_READY", "stepSequence": 1,
            "configVersion": start["configVersion"]}
        final = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
        assert final["measurementUid"] != initial["measurementUid"]
        assert final["cleanActionSequence"] == 1
        assert final["measurementKind"] == {"stable": "STABLE_MEAN", "median": "TIMEOUT_MEDIAN", "unavailable": "UNAVAILABLE"}[mode]
        assert final["reportedWeightGrams"] == {"stable": 123, "median": 300, "unavailable": 0}[mode]
        assert final["sampleCount"] == {"stable": 5, "median": 20, "unavailable": 0}[mode]
        assert final["faultCode"] == ("WEIGHT_TIMEOUT" if mode == "unavailable" else "NONE")
        assert final["measurementElapsedMs"] == (1020 if mode == "stable" else 5000)
        state = exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=now)[0][1]
        assert state["status"] == "RUNNING" and state["phase"] == "CLEAN_RESULT_CONFIRMATION"
        assert not facts(runtime, now)["cleanLockPowered"]
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()


def test_reopening_after_final_weight_invalidates_candidate_and_next_finish_measures_again(runtime, tmp_path):
    clean, start, initial, _, now = active_clean(runtime, tmp_path)
    store = EdgeStore(str(tmp_path / "new-candidate.db"))
    store.initialize()
    try:
        assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
        save_intent(runtime, start, "CLEAN_FINISH_REQUESTED", 1, now, store)
        now = advance(runtime, now, 0)
        now = take_samples(runtime, [123] * 5, start=now, measurement=2)
        assert not request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now, after=1)  # Final not SAVED.
        old = save_intent(runtime, start, "CLEAN_FINAL_WEIGHT_READY", 1, now, store)
        assert not request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now, after=0)
        assert not request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now, after=1)  # Confirmation is a separate fact.
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
        assert new["measurementUid"] != old["measurementUid"] != initial["measurementUid"]
        assert new["reportedWeightGrams"] == 456 and old["reportedWeightGrams"] == 123
        assert store.get_native_process_event("CLEAN_FINAL_WEIGHT_READY", 42, old["mcuEventSequence"])["payload"] == uart.encode_payload("CLEAN_FINAL_WEIGHT_READY", old)
        assert store.list_native_result_report_tasks() == [] and not facts(runtime, now)["cleanLockPowered"]
    finally:
        store.close()


@pytest.mark.parametrize("message", ["CLEAN_UNLOCK_REQUESTED", "CLEAN_FINISH_REQUESTED", "CLEAN_FINAL_WEIGHT_READY"])
@pytest.mark.parametrize("loss", ["exception", "short", "lost", "reply_lost"])
def test_real_pi_handoff_commits_original_before_saved_and_recovers_without_reopening_or_remeasurement(runtime, tmp_path, message, loss):
    from mcu_process_handoff import McuProcessEventHandoff
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    lib, endpoint, preparation, replies, *_ = runtime
    path = str(tmp_path / "pi-restarted.db")
    store = EdgeStore(path)
    store.initialize()
    if message == "CLEAN_FINAL_WEIGHT_READY":
        assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
        save_intent(runtime, start, "CLEAN_FINISH_REQUESTED", 1, now, store)
        now = advance(runtime, now, 0)
        now = take_samples(runtime, [123] * 5, start=now, measurement=2)
    else:
        assert request(runtime, clean, start, message, now)
    scope = original_scope(start, clean=True) | {"eventMessageType": message, "stepSequence": 1,
        "configVersion": start["configVersion"]}
    raw_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
    client_scope = {key: value for key, value in scope.items() if key != "queryId"}
    original = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    raw = uart.encode_payload(message, original)
    assert store.acquire_work_slot("CLEAN", start["operationUid"], 1, {"phase": "NATIVE_CLEAN"})
    occupancy = store.get_work_slot()
    broken, sent = True, []

    def write(frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        name = decoded["messageName"]
        sent.append(name)
        if name == "PROCESS_EVENT_SAVED":
            reader = EdgeStore(path)
            reader.initialize()
            try:
                row = reader.get_native_process_receipt(raw_scope)
                assert row["payload"] == raw and row["saved_payload"] == decoded["payload"]
                assert reader.get_work_slot() == occupancy and reader.list_native_result_report_tasks() == []
            finally:
                reader.close()
            if broken:
                if loss == "exception":
                    raise OSError("synthetic process confirmation loss")
                if loss == "short":
                    return 1
                if loss == "lost":
                    return len(frame)
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        if broken and name == "PROCESS_EVENT_SAVED" and loss == "reply_lost":
            replies.clear()
        return len(frame)

    def pump(client):
        while replies:
            client.accept_frame(replies.pop(0), now)

    try:
        replies.clear()
        client = McuProcessEventHandoff(store, write, client_scope)
        first_query = client.poll(now)
        pump(client)
        assert client.last_write_error == {"exception": "WRITE_FAILED", "short": "SHORT_WRITE"}.get(loss)
        assert store.get_native_process_receipt(raw_scope)["payload"] == raw
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        client = McuProcessEventHandoff(store, write, client_scope)
        now = advance(runtime, now, 1000, poll=False)
        assert client.poll(now) > first_query
        pump(client)
        now = advance(runtime, now, 1000, poll=False)
        client.poll(now)
        pump(client)
        assert client.observation(now)["status"] == "RELEASED"
        assert set(sent) == {"QUERY_PROCESS_EVENT", "PROCESS_EVENT_SAVED"}
        assert store.get_native_process_receipt(raw_scope)["payload"] == raw
        assert store.get_work_slot() == occupancy and store.list_native_result_report_tasks() == []
        assert not facts(runtime, now)["cleanLockPowered"]
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        expected_measurement = 1 if message == "CLEAN_UNLOCK_REQUESTED" else 2
        assert facts(runtime, now)["measurementSequence"] == expected_measurement
        now = advance(runtime, now, 100)
        assert facts(runtime, now)["measurementSequence"] == expected_measurement
        assert exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=now)[0][1]["status"] == "RUNNING"
    finally:
        store.close()


@pytest.mark.parametrize("message", ["CLEAN_UNLOCK_REQUESTED", "CLEAN_FINISH_REQUESTED"])
def test_pi_database_failure_keeps_mcu_intent_held_and_cannot_start_next_action(runtime, tmp_path, message):
    import sqlite3
    from mcu_process_handoff import McuProcessEventHandoff
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, message, now)
    lib, endpoint, _, replies, *_ = runtime
    scope = {key: value for key, value in original_scope(start, clean=True).items() if key != "queryId"} | {
        "eventMessageType": message, "stepSequence": 1, "configVersion": start["configVersion"]}
    store = EdgeStore(str(tmp_path / "failed-pi-commit.db"))
    store.initialize()
    sent = []

    def write(frame):
        sent.append(uart.decode_frame(frame, sender_role="EDGE")["messageName"])
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        return len(frame)

    try:
        replies.clear()
        client = McuProcessEventHandoff(store, write, scope)
        client.poll(now)
        assert client.accept_frame(replies.pop(0), now)
        body = replies.pop(0)
        store._conn.set_authorizer(lambda action, first, *_:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            client.accept_frame(body, now)
        store._conn.set_authorizer(None)
        assert sent == ["QUERY_PROCESS_EVENT"]
        assert store.get_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", scope | {"queryId": 1})[8:]) is None
        now = advance(runtime, now, 100)
        assert facts(runtime, now)["measurementSequence"] == 1 and not facts(runtime, now)["cleanLockPowered"]
        assert exchange(runtime, "QUERY_PROCESS_EVENT", scope | {"queryId": 1}, now=now)[0][1]["status"] == "HELD"
        assert store.list_native_result_report_tasks() == []
    finally:
        store._conn.set_authorizer(None)
        store.close()
