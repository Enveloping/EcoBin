"""Original clean command -> real C solenoid timer -> exact Pi evidence custody."""
import ctypes as c

import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, original_scope
from hardware.tests.test_mcu_opening_gate import prepared, grant
from hardware.tests.test_mcu_delivery_execution import enable as enable_delivery, events, facts
from hardware.tests.test_mcu_actuator_event_journal import Reservation


def enable(runtime):
    lib, endpoint, preparation, *_ = runtime
    owner = (c.c_uint64 * 64)()
    assert lib.McuCleanExecution_Attach(owner, preparation, endpoint)
    return owner


def advance(runtime, now, duration, *, poll=True):
    lib, endpoint, preparation, *_ = runtime
    lib.RuntimeClock_Advance(duration)
    lib.ActuatorRuntime_Tick()
    now += duration
    if poll:
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    return now


def test_first_clean_unlock_executes_once_and_preserves_both_edges_without_waiting_for_save(runtime, tmp_path):
    delivery, clean = enable_delivery(runtime), enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    assert lib.ActuatorRuntime_SetDoorTarget(1)  # Existing logical CLOSE, not physical limit proof.
    start, initial, now = prepared(runtime, tmp_path, clean=True)
    name, command = grant(start, initial, clean=True)
    accepted = exchange(runtime, name, command, now=now)[0][1]
    assert accepted["outcome"] == "ACCEPTED"
    assert facts(runtime, now)["cleanLockPowered"]
    assert exchange(runtime, name, command, now=now)[0][1] == accepted
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    on = events(runtime, now)[1][1]
    assert on["lockPowerState"] == "ENERGIZED" and on["solenoidHealth"] == "UNKNOWN"
    assert on["mcuCommandUid"] == command["mcuCommandUid"]
    assert on["operationUid"] == start["operationUid"] and on["uptimeMs"] == now
    now = advance(runtime, now, command["unlockPulseMs"], poll=False)
    assert not facts(runtime, now)["cleanLockPowered"]
    assert exchange(runtime, name, command, now=now)[0][1] == accepted
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    assert events(runtime, now)[1][1] == on
    store = EdgeStore(str(tmp_path / "clean-actions.db"))
    store.initialize()
    try:
        receipt = store.save_native_actuator_event("CLEAN_LOCK_POWER_CHANGED", uart.encode_payload("CLEAN_LOCK_POWER_CHANGED", on))
        assert exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        off = events(runtime, now)[1][1]
        assert off["lockPowerState"] == "DEENERGIZED" and off["solenoidHealth"] == "UNKNOWN"
        assert off["uptimeMs"] == now and off["mcuEventSequence"] == on["mcuEventSequence"] + 1
        assert off["mcuCommandUid"] == command["mcuCommandUid"] and off["operationUid"] == start["operationUid"]
        state = exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=now)[0][1]
        assert state["status"] == "RUNNING" and state["phase"] == "CLEAN_ACTIVE"
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()
    assert delivery and clean


@pytest.mark.parametrize("saved,door,guard_error,reserved,expected", [
    (False, 1, 0, 0, "BUSY"), (True, None, 0, 0, "STATE_CONFLICT"),
    (True, 2, 0, 0, "STATE_CONFLICT"), (True, 1, 11, 0, "SAFETY_BLOCKED"),
    (True, 1, 0, 7, "BUSY"), (True, 1, 0, 6, "BUSY"),
])
def test_rejected_clean_unlock_is_immutable_and_does_not_leak_power_or_reservation(runtime, tmp_path,
        saved, door, guard_error, reserved, expected):
    clean = enable(runtime)
    lib, endpoint, _, _, prerequisites, *_ = runtime
    if door:
        assert lib.ActuatorRuntime_SetDoorTarget(door)
    start, initial, now = prepared(runtime, tmp_path, clean=True, saved=saved)
    reservation = Reservation()
    if reserved:
        assert lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, reserved, c.byref(reservation))
    prerequisites["error"] = guard_error
    name, command = grant(start, initial, clean=True)
    decision = exchange(runtime, name, command, now=now)[0][1]
    assert decision["outcome"] == "REJECTED" and decision["errorCode"] == expected
    prerequisites["error"] = 0
    if reserved:
        assert lib.McuControlEndpoint_CancelActuatorEvents(endpoint, c.byref(reservation))
    assert lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, 8, c.byref(reservation))
    assert lib.McuControlEndpoint_CancelActuatorEvents(endpoint, c.byref(reservation))
    assert lib.ActuatorRuntime_SetDoorTarget(1)
    now = advance(runtime, now, 1000)
    assert exchange(runtime, name, command, now=now)[0][1] == decision
    assert not facts(runtime, now)["cleanLockPowered"]
    assert events(runtime, now)[0][1]["status"] == "NOT_FOUND" and clean


@pytest.mark.parametrize("change,expected", [
    ({"cleanActionSequence": 1}, "STATE_CONFLICT"),
    ({"recoveryGeneration": 1}, "STATE_CONFLICT"),
    ({"operationUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}, "UNKNOWN_WORK"),
    ({"parentCommandUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}, "UNKNOWN_WORK"),
    ({"unlockPulseMs": 1001}, "STATE_CONFLICT"),
])
def test_other_operation_reopen_or_changed_pulse_cannot_use_first_unlock_path(runtime, tmp_path, change, expected):
    clean, delivery = enable(runtime), enable_delivery(runtime)
    lib, *_ = runtime
    assert lib.ActuatorRuntime_SetDoorTarget(1)
    start, initial, now = prepared(runtime, tmp_path, clean=True)
    name, command = grant(start, initial, clean=True, **change)
    decision = exchange(runtime, name, command, now=now)[0][1]
    assert decision["outcome"] == "REJECTED" and decision["errorCode"] == expected
    assert not facts(runtime, now)["cleanLockPowered"]
    assert events(runtime, now)[0][1]["status"] == "NOT_FOUND" and clean and delivery


@pytest.mark.parametrize("stop,late", [(False, 0), (False, 123), (True, 0)])
def test_absent_foreground_keeps_original_power_times_and_pinch_is_not_a_clean_lock_fault(runtime, tmp_path, stop, late):
    clean = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    assert lib.ActuatorRuntime_SetDoorTarget(1)
    lib.TestFacts_Pinch(1)
    start, initial, now = prepared(runtime, tmp_path, clean=True)
    name, command = grant(start, initial, clean=True)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    on_at = now
    now = advance(runtime, now, 200 if stop else command["unlockPulseMs"] + late, poll=False)
    if stop:
        lib.ActuatorRuntime_StopForUpdate()
    off_at = now
    assert not facts(runtime, now)["cleanLockPowered"]
    now = advance(runtime, now, 10000, poll=False)
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    on = events(runtime, now)[1][1]
    following = exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=102, targetMcuBootId=42,
        afterMcuEventSequence=on["mcuEventSequence"]), now=now)[1][1]
    assert on["uptimeMs"] == on_at and on["lockPowerState"] == "ENERGIZED"
    assert following["uptimeMs"] == off_at and following["lockPowerState"] == "DEENERGIZED"
    assert following["solenoidHealth"] == on["solenoidHealth"] == "UNKNOWN"
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    assert not facts(runtime, now)["cleanLockPowered"]
    assert exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=now)[0][1]["phase"] == (
        "CLEAN_RECOVERY_REQUIRED" if stop else "CLEAN_ACTIVE")
    assert clean


@pytest.mark.parametrize("room", [1, 2, 3])
def test_clean_pulse_reserves_power_pair_and_independent_interruption_number_before_energizing(runtime, tmp_path, room):
    clean = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    assert lib.ActuatorRuntime_SetDoorTarget(1)
    start, initial, now = prepared(runtime, tmp_path, clean=True)
    lib.TestPreparation_SetEventSequence(endpoint, 2**32 - 1 - room)
    name, command = grant(start, initial, clean=True)
    decision = exchange(runtime, name, command, now=now)[0][1]
    assert decision["outcome"] == ("ACCEPTED" if room == 3 else "REJECTED")
    if room < 3:
        assert decision["errorCode"] == "BUSY" and not facts(runtime, now)["cleanLockPowered"]
    else:
        assert not lib.McuControlEndpoint_ReserveEventSequence(endpoint)
        now = advance(runtime, now, command["unlockPulseMs"])
        on = events(runtime, now)[1][1]
        off = exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=103, targetMcuBootId=42,
            afterMcuEventSequence=on["mcuEventSequence"]), now=now)[1][1]
        assert on["mcuEventSequence"] == 2**32 - 3 and off["mcuEventSequence"] == 2**32 - 2
        assert not lib.McuControlEndpoint_ReserveEventSequence(endpoint)
        lib.ActuatorRuntime_StopForUpdate()
        now = advance(runtime, now, 0)
        cause = exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=104, targetMcuBootId=42,
            afterMcuEventSequence=off["mcuEventSequence"]), now=now)[1]
        assert cause[0] == "CLEAN_OPERATION_INTERRUPTED" and cause[1]["mcuEventSequence"] == 2**32 - 1
        assert not facts(runtime, now)["cleanLockPowered"]
    assert preparation and clean


@pytest.mark.parametrize("limit", ["startExecutionWindowMs", "operationWindowMs"])
def test_first_clean_unlock_cannot_extend_original_start_or_operation_deadline(runtime, tmp_path, limit):
    clean = enable(runtime)
    lib, *_ = runtime
    assert lib.ActuatorRuntime_SetDoorTarget(1)
    start, initial, now = prepared(runtime, tmp_path, clean=True, **{limit: 2000})
    name, command = grant(start, initial, clean=True)
    now = advance(runtime, now, 2000 - now)
    decision = exchange(runtime, name, command, now=now)[0][1]
    assert decision["outcome"] == "REJECTED" and decision["errorCode"] == "EXPIRED"
    assert not facts(runtime, now)["cleanLockPowered"]
    assert events(runtime, now)[0][1]["status"] == "NOT_FOUND" and clean


def test_clean_and_delivery_executors_attach_once_before_binding_and_preserve_delivery_path(runtime, tmp_path):
    clean, delivery = enable(runtime), enable_delivery(runtime)
    lib, endpoint, preparation, *_ = runtime
    duplicate = (c.c_uint64 * 64)()
    assert not lib.McuCleanExecution_Attach(duplicate, preparation, endpoint)
    assert not lib.McuDeliveryExecution_Attach(duplicate, preparation, endpoint)
    start, initial, now = prepared(runtime, tmp_path)
    assert not lib.McuCleanExecution_Attach(duplicate, preparation, endpoint)
    name, command = grant(start, initial)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    now = advance(runtime, now, 100)
    assert events(runtime, now)[1][0] == "DELIVERY_DOOR_COMMAND_RESULT"
    assert facts(runtime, now)["pb6Output"] and not facts(runtime, now)["cleanLockPowered"]
    assert clean and delivery


@pytest.mark.parametrize("edge", ["ENERGIZED", "DEENERGIZED"])
@pytest.mark.parametrize("loss", ["exception", "short", "lost", "reply_lost"])
def test_real_clean_edges_survive_pi_restart_and_confirmation_loss_without_reopening(runtime, tmp_path, edge, loss):
    from mcu_actuator_handoff import McuActuatorEventHandoff
    clean = enable(runtime)
    lib, endpoint, preparation, replies, *_ = runtime
    assert lib.ActuatorRuntime_SetDoorTarget(1)
    start, initial, now = prepared(runtime, tmp_path, clean=True)
    name, command = grant(start, initial, clean=True)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    now = advance(runtime, now, command["unlockPulseMs"])
    on = events(runtime, now)[1][1]
    off = exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=104, targetMcuBootId=42,
        afterMcuEventSequence=on["mcuEventSequence"]), now=now)[1][1]
    original = {event["mcuEventSequence"]: uart.encode_payload("CLEAN_LOCK_POWER_CHANGED", event) for event in (on, off)}
    path = str(tmp_path / "clean-pi.db")
    store = EdgeStore(path)
    store.initialize()
    assert store.acquire_work_slot("CLEAN", start["operationUid"], 1, {"phase": "NATIVE_CLEAN"})
    occupancy = store.get_work_slot()
    broken, sent = True, []

    def write(frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        message = decoded["messageName"]
        sent.append(message)
        drop = False
        if message == "ACTUATOR_EVENT_SAVED":
            receipt = uart.decode_payload(message, decoded["payload"])
            reader = EdgeStore(path)
            reader.initialize()
            try:
                row = reader.get_native_actuator_event(42, receipt["mcuEventSequence"])
                assert row["payload"] == original[receipt["mcuEventSequence"]]
                assert row["saved_payload"] == decoded["payload"]
                assert reader.get_work_slot() == occupancy and reader.list_native_result_report_tasks() == []
                drop = broken and uart.decode_payload(row["message_name"], row["payload"])["lockPowerState"] == edge
            finally:
                reader.close()
            if drop:
                if loss == "exception":
                    raise OSError("synthetic clean-save transport loss")
                if loss == "short":
                    return 1
                if loss == "lost":
                    return len(frame)
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        if drop and loss == "reply_lost":
            replies.clear()
        return len(frame)

    def pump(client):
        while replies:
            client.accept_frame(replies.pop(0), now)

    try:
        replies.clear()
        client = McuActuatorEventHandoff(store, write, 42)
        for _ in range(2):
            client.poll(now)
            pump(client)
            now = advance(runtime, now, 1000)
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        client = McuActuatorEventHandoff(store, write, 42)
        for _ in range(3):
            client.poll(now)
            pump(client)
            now = advance(runtime, now, 1000)
        assert client.observation(now) is None  # Expired observation is not idle evidence.
        client.poll(now)
        pump(client)
        assert client.observation(now)["status"] == "NOT_FOUND"
        assert not facts(runtime, now)["cleanLockPowered"]
        assert store.get_work_slot() == occupancy and store.list_native_result_report_tasks() == []
        assert set(sent) <= {"QUERY_ACTUATOR_EVENT", "ACTUATOR_EVENT_SAVED"}
        for sequence, raw in original.items():
            assert store.get_native_actuator_event(42, sequence)["payload"] == raw
        assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
        assert not facts(runtime, now)["cleanLockPowered"]
        assert facts(runtime, now)["measurementSequence"] == 1
        assert events(runtime, now)[0][1]["status"] == "NOT_FOUND" and preparation and clean
    finally:
        store.close()
