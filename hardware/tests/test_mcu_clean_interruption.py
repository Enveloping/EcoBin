"""Actual C clean interruption evidence remains recovery-only after exact Pi custody."""
import pytest
import ctypes as c
import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, original_scope
from hardware.tests.test_mcu_clean_intent import active_clean, request, save_intent
from hardware.tests.test_mcu_clean_execution import enable, advance
from hardware.tests.test_mcu_delivery_execution import events, facts
from hardware.tests.test_mcu_opening_gate import prepared, grant
from hardware.tests.test_mcu_work_preparation import take_samples
from hardware.tests.test_mcu_clean_confirmation import measured_candidate, confirm, held_result
from hardware.tests.test_mcu_actuator_event_journal import Reservation
from mcu_actuator_handoff import McuActuatorEventHandoff

NAME = "CLEAN_OPERATION_INTERRUPTED"


def work(runtime, start, now):
    return exchange(runtime, "QUERY_WORK", original_scope(start, clean=True), now=now)[0][1]


@pytest.mark.parametrize("reopen", [False, True])
def test_expiry_between_acceptance_and_final_dispatch_retains_acceptance_and_no_fake_power_edges(runtime, tmp_path, reopen):
    lib, endpoint, preparation, _, prerequisites, *_ = runtime
    if reopen:
        clean, start, initial, _, now = active_clean(runtime, tmp_path)
        store = EdgeStore(str(tmp_path / "reopen-dispatch.db"))
        store.initialize()
        try:
            assert request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now)
            save_intent(runtime, start, "CLEAN_UNLOCK_REQUESTED", 1, now, store)
        finally:
            store.close()
    else:
        clean = enable(runtime)
        assert lib.ActuatorRuntime_SetDoorTarget(1)
        start, initial, now = prepared(runtime, tmp_path, clean=True)
    name, command = grant(start, initial, clean=True, remainingOperationWindowMs=100,
        cleanActionSequence=1 if reopen else 0, commandSequence=8 if reopen else 7,
        mcuCommandUid="88888888-8888-4888-8888-888888888888" if reopen else "77777777-7777-4777-8777-777777777777")
    # The three read snapshots pass, then a timer preemption reaches the exact
    # deadline at BeginCleanPulse's final critical-section check.
    prerequisites["inspect"] = lambda *_: lib.TestFacts_AdvanceOnEntry(4, 100)
    accepted = exchange(runtime, name, command, now=now)[0][1]
    prerequisites.pop("inspect")
    now += 100
    assert accepted["outcome"] == "ACCEPTED"
    assert not facts(runtime, now)["cleanLockPowered"]
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    interrupted_name, event = events(runtime, now)[1]
    assert interrupted_name == NAME and event["interruptionReason"] == "UNLOCK_DISPATCH_REJECTED"
    assert event["interruptedPhase"] == ("CLEAN_ACTIVE" if reopen else "CLEAN_WAIT_FIRST_UNLOCK")
    assert event["mcuCommandUid"] == command["mcuCommandUid"] and event["finalMeasurementEventSequence"] == 0
    assert exchange(runtime, name, command, now=now)[0][1] == accepted
    assert work(runtime, start, now)["phase"] == "CLEAN_RECOVERY_REQUIRED" and clean


def test_update_during_active_clean_keeps_original_cause_without_implying_manual_close(runtime, tmp_path):
    clean, start, _, command, now = active_clean(runtime, tmp_path)
    lib, endpoint, preparation, *_ = runtime
    lib.ActuatorRuntime_StopForUpdate()
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    frames = events(runtime, now)
    assert frames[0][1]["status"] == "HELD"
    name, event = frames[1]
    assert name == NAME
    assert event == dict(mcuBootId=42, mcuEventSequence=event["mcuEventSequence"], uptimeMs=now,
        mcuCommandUid=command["mcuCommandUid"], operationUid=start["operationUid"], portNo=1,
        cleanActionSequence=0, interruptedPhase="CLEAN_ACTIVE", interruptionReason="UPDATE_STOPPED",
        finalMeasurementEventSequence=0)
    store = EdgeStore(str(tmp_path / "interruption.db"))
    store.initialize()
    try:
        assert store.acquire_work_slot("CLEAN", start["operationUid"], 1, {"phase": "CLEAN_ACTIVE"})
        occupancy = store.get_work_slot()
        receipt = store.save_native_actuator_event(name, uart.encode_payload(name, event))
        assert exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        assert work(runtime, start, now)["phase"] == "CLEAN_RECOVERY_REQUIRED"
        assert work(runtime, start, now)["status"] == "RUNNING"
        assert not request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now)
        assert store.get_work_slot() == occupancy and store.list_native_result_report_tasks() == []
    finally:
        store.close()


def test_unused_interruption_reservation_is_released_only_after_normal_confirmation_custody(runtime, tmp_path):
    clean, start, _, final, now, store = measured_candidate(runtime, tmp_path)
    lib, endpoint, *_ = runtime
    try:
        assert confirm(runtime, clean, start, final, now)
        reservation = Reservation()
        assert not lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, 8, c.byref(reservation))
        save_intent(runtime, start, "CLEAN_COMPLETION_CONFIRMED", 1, now, store)
        now = advance(runtime, now, 0)
        assert held_result(runtime, start, now)["physicalCloseConfirmed"]
        assert lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, 8, c.byref(reservation))
        assert lib.McuControlEndpoint_CancelActuatorEvents(endpoint, c.byref(reservation))
    finally:
        store.close()


@pytest.mark.parametrize("stage", ["pulse", "active", "measuring", "candidate"])
@pytest.mark.parametrize("cause", ["expiry", "context_lost"])
def test_expiry_or_control_loss_retains_cause_without_inventing_update_or_missing_edges(runtime, tmp_path, stage, cause):
    lib, endpoint, preparation, *_ = runtime
    store = EdgeStore(str(tmp_path / "fault.db"))
    store.initialize()
    try:
        if stage == "pulse":
            clean = enable(runtime)
            assert lib.ActuatorRuntime_SetDoorTarget(1)
            start, initial, now = prepared(runtime, tmp_path, clean=True)
            name, command = grant(start, initial, clean=True, remainingOperationWindowMs=100)
            assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
        else:
            clean, start, _, command, now = active_clean(runtime, tmp_path)
            if stage in ("measuring", "candidate"):
                assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
                save_intent(runtime, start, "CLEAN_FINISH_REQUESTED", 1, now, store)
                now = advance(runtime, now, 0)
                now = take_samples(runtime, [123] * (5 if stage == "candidate" else 1), start=now, measurement=2)
        if cause == "context_lost":
            lib.TestFacts_ReinitializeActuator()  # Hardware/control boundary only, NOT MCU reboot.
        else:
            now = advance(runtime, now, 100 if stage == "pulse" else 200000, poll=False)
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        if stage == "pulse" and cause == "expiry":
            for power in ("ENERGIZED", "DEENERGIZED"):
                name, event = events(runtime, now)[1]
                assert name == "CLEAN_LOCK_POWER_CHANGED" and event["lockPowerState"] == power
                receipt = store.save_native_actuator_event(name, uart.encode_payload(name, event))
                exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)
        # Lost runtime context does not manufacture ON/OFF that was never retained.
        name, event = events(runtime, now)[1]
        assert name == NAME and event["interruptionReason"] == ("OPERATION_EXPIRED" if cause == "expiry" else "CONTROL_CONTEXT_LOST")
        assert event["interruptedPhase"] == dict(pulse="CLEAN_UNLOCK_PULSE", active="CLEAN_ACTIVE",
            measuring="CLEAN_FINAL_MEASURING", candidate="CLEAN_RESULT_CONFIRMATION")[stage]
        assert not facts(runtime, now)["cleanLockPowered"]
        assert work(runtime, start, now)["phase"] == "CLEAN_RECOVERY_REQUIRED"
        if stage in ("measuring", "candidate"):
            final = save_intent(runtime, start, "CLEAN_FINAL_WEIGHT_READY", 1, now, store)
            assert event["finalMeasurementEventSequence"] == final["mcuEventSequence"]
        assert clean
    finally:
        store.close()


@pytest.mark.parametrize("stage", ["pulse", "measuring", "candidate"])
def test_update_retains_original_phase_edges_and_terminal_weight_before_recovery(runtime, tmp_path, stage):
    lib, endpoint, preparation, *_ = runtime
    store = EdgeStore(str(tmp_path / "stage.db"))
    store.initialize()
    try:
        if stage == "pulse":
            clean = enable(runtime)
            assert lib.ActuatorRuntime_SetDoorTarget(1)
            start, initial, now = prepared(runtime, tmp_path, clean=True)
            name, command = grant(start, initial, clean=True)
            assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
            on_at = now
            now = advance(runtime, now, 200, poll=False)
        else:
            clean, start, initial, command, now = active_clean(runtime, tmp_path)
            assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
            save_intent(runtime, start, "CLEAN_FINISH_REQUESTED", 1, now, store)
            now = advance(runtime, now, 0)
            now = take_samples(runtime, [123] * (5 if stage == "candidate" else 1), start=now, measurement=2)
        lib.ActuatorRuntime_StopForUpdate()
        interrupted_at = now
        now = advance(runtime, now, 300)
        if stage == "pulse":
            for power, at in [("ENERGIZED", on_at), ("DEENERGIZED", interrupted_at)]:
                name, event = events(runtime, now)[1]
                assert name == "CLEAN_LOCK_POWER_CHANGED" and event["lockPowerState"] == power and event["uptimeMs"] == at
                receipt = store.save_native_actuator_event(name, uart.encode_payload(name, event))
                assert exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
            final_sequence = 0
        else:
            final = save_intent(runtime, start, "CLEAN_FINAL_WEIGHT_READY", 1, now, store)
            assert final["measurementKind"] == ("STABLE_MEAN" if stage == "candidate" else "INTERRUPTED")
            assert final["sampleCount"] == (5 if stage == "candidate" else 1)
            final_sequence = final["mcuEventSequence"]
            assert not confirm(runtime, clean, start, final, now)
        name, event = events(runtime, now)[1]
        assert name == NAME and event["finalMeasurementEventSequence"] == final_sequence
        assert event["interruptedPhase"] == {"pulse": "CLEAN_UNLOCK_PULSE", "measuring": "CLEAN_FINAL_MEASURING",
            "candidate": "CLEAN_RESULT_CONFIRMATION"}[stage]
        assert event["uptimeMs"] == now and event["interruptionReason"] == "UPDATE_STOPPED"
        assert event["mcuCommandUid"] == command["mcuCommandUid"]
        assert work(runtime, start, now)["phase"] == "CLEAN_RECOVERY_REQUIRED"
        assert not facts(runtime, now)["cleanLockPowered"]
    finally:
        store.close()


@pytest.mark.parametrize("loss", ["short", "exception", "lost", "reply_lost"])
@pytest.mark.parametrize("measuring", [False, True])
def test_actual_pi_restart_and_lost_receipt_only_requeries_interruption_and_keeps_occupancy(runtime, tmp_path, loss, measuring):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    lib, endpoint, preparation, replies, *_ = runtime
    path = str(tmp_path / "pi-custody.db")
    store = EdgeStore(path)
    store.initialize()
    assert store.acquire_work_slot("CLEAN", start["operationUid"], 1, {"phase": "NATIVE_CLEAN"})
    occupied = store.get_work_slot()
    if measuring:
        assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
        save_intent(runtime, start, "CLEAN_FINISH_REQUESTED", 1, now, store)
        now = advance(runtime, now, 0)
        now = take_samples(runtime, [123], start=now, measurement=2)
    lib.ActuatorRuntime_StopForUpdate()
    now = advance(runtime, now, 0)
    _, event = events(runtime, now)[1]
    raw = uart.encode_payload(NAME, event)
    sent, broken = [], True

    def write(frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        name = decoded["messageName"]
        sent.append(name)
        if name == "ACTUATOR_EVENT_SAVED":
            # Verify actual committed custody from a separate SQLite connection,
            # before a single receipt byte is delivered to actual C ingress.
            reader = EdgeStore(path)
            reader.initialize()
            try:
                row = reader.get_native_actuator_event(42, event["mcuEventSequence"])
                assert row["payload"] == raw and row["saved_payload"] == decoded["payload"]
                assert reader.get_work_slot() == occupied and reader.list_native_result_report_tasks() == []
            finally:
                reader.close()
            if broken:
                if loss == "exception":
                    raise OSError("synthetic clean-interruption receipt write failure")
                if loss == "short":
                    return 1
                if loss == "lost":
                    return len(frame)
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        if name == "ACTUATOR_EVENT_SAVED" and broken and loss == "reply_lost":
            replies.clear()
        return len(frame)

    def pump(client):
        while replies:
            client.accept_frame(replies.pop(0), now)

    try:
        replies.clear()
        client = McuActuatorEventHandoff(store, write, 42)
        first_query = client.poll(now)
        pump(client)
        assert sent.count("ACTUATOR_EVENT_SAVED") == 1
        store.close()
        store = EdgeStore(path)
        store.initialize()
        broken = False
        client = McuActuatorEventHandoff(store, write, 42)
        now = advance(runtime, now, 1000)
        assert client.poll(now) > first_query
        pump(client)
        now = advance(runtime, now, 1000)
        client.poll(now)
        pump(client)
        assert client.observation(now)["status"] == "NOT_FOUND"
        assert store.get_native_actuator_event(42, event["mcuEventSequence"])["payload"] == raw
        if measuring:
            final = save_intent(runtime, start, "CLEAN_FINAL_WEIGHT_READY", 1, now, store)
            assert final["measurementKind"] == "INTERRUPTED" and final["sampleCount"] == 1
            assert final["mcuEventSequence"] == event["finalMeasurementEventSequence"]
        assert store.get_work_slot() == occupied and store.list_native_result_report_tasks() == []
        assert work(runtime, start, now)["phase"] == "CLEAN_RECOVERY_REQUIRED"
        assert facts(runtime, now)["measurementSequence"] == (2 if measuring else 1)
        assert not facts(runtime, now)["cleanLockPowered"]
        assert set(sent) <= {"QUERY_ACTUATOR_EVENT", "ACTUATOR_EVENT_SAVED"}
    finally:
        store.close()


def test_failed_sqlite_save_never_confirms_or_ends_interrupted_clean(runtime, tmp_path):
    import sqlite3
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    lib, endpoint, _, replies, *_ = runtime
    lib.ActuatorRuntime_StopForUpdate()
    now = advance(runtime, now, 0)
    original = events(runtime, now)[1][1]
    store = EdgeStore(str(tmp_path / "failed-save.db"))
    store.initialize()
    sent = []
    def write(frame):
        sent.append(uart.decode_frame(frame)["messageName"])
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now) == 1
        return len(frame)
    try:
        store._conn.execute("CREATE TEMP TRIGGER fail_clean_interrupt BEFORE INSERT ON native_actuator_event BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END")
        replies.clear()
        client = McuActuatorEventHandoff(store, write, 42)
        client.poll(now)
        client.accept_frame(replies.pop(0), now)
        with pytest.raises(sqlite3.DatabaseError):
            client.accept_frame(replies.pop(0), now)
        assert sent == ["QUERY_ACTUATOR_EVENT"]
        assert events(runtime, now)[1][1] == original
        assert store.get_native_actuator_event(42, original["mcuEventSequence"]) is None
        assert work(runtime, start, now)["phase"] == "CLEAN_RECOVERY_REQUIRED" and clean
    finally:
        store.close()


@pytest.mark.parametrize("changes", [{"interruptionReason": "OPERATION_EXPIRED"}, {"mcuCommandUid": "99999999-9999-4999-8999-999999999999"}])
def test_conflicting_interruption_cannot_replace_durable_original_or_get_confirmation(runtime, tmp_path, changes):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    lib, *_ = runtime
    lib.ActuatorRuntime_StopForUpdate()
    now = advance(runtime, now, 0)
    _, event = events(runtime, now)[1]
    raw = uart.encode_payload(NAME, event)
    path = str(tmp_path / "conflict.db")
    store = EdgeStore(path)
    store.initialize()
    try:
        store.save_native_actuator_event(NAME, raw)
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_actuator_event(NAME, uart.encode_payload(NAME, event | changes))
        store.close()
        store = EdgeStore(path)
        store.initialize()
        with pytest.raises(ValueError, match="conflict"):
            store.save_native_actuator_event(NAME, raw)
        conflicts = store.list_native_actuator_event_conflicts()
        assert len(conflicts) == 1 and conflicts[0]["payload"] == uart.encode_payload(NAME, event | changes)
        sent = []
        client = McuActuatorEventHandoff(store, lambda frame: sent.append(frame) or len(frame), 42)
        query_id = client.poll(now)
        # Fresh real-C query of the original is still blocked by durable conflict.
        exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=query_id, targetMcuBootId=42, afterMcuEventSequence=0), now=now)
        with pytest.raises(ValueError, match="conflict"):
            client.accept_frame(runtime[3][0], now)
        assert [uart.decode_frame(frame)["messageName"] for frame in sent] == ["QUERY_ACTUATOR_EVENT"]
        assert events(runtime, now)[1][1] == event
        assert work(runtime, start, now)["phase"] == "CLEAN_RECOVERY_REQUIRED" and clean
    finally:
        store.close()


@pytest.mark.parametrize("grant_reopen", [False, True])
def test_reopened_candidate_is_not_reused_by_interruption_and_latest_accepted_grant_is_named(runtime, tmp_path, grant_reopen):
    clean, start, initial, final, now, store = measured_candidate(runtime, tmp_path)
    lib, *_ = runtime
    try:
        old_raw = uart.encode_payload("CLEAN_FINAL_WEIGHT_READY", final)
        assert request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now, after=1)
        # Keep the new intent HELD or explicitly save and execute another grant.
        if grant_reopen:
            save_intent(runtime, start, "CLEAN_UNLOCK_REQUESTED", 2, now, store)
            name, command = grant(start, initial, clean=True, cleanActionSequence=2, commandSequence=8,
                mcuCommandUid="88888888-8888-4888-8888-888888888888")
            assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
            now = advance(runtime, now, command["unlockPulseMs"])
            for _ in range(2):
                name, edge = events(runtime, now)[1]
                receipt = store.save_native_actuator_event(name, uart.encode_payload(name, edge))
                exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)
        lib.ActuatorRuntime_StopForUpdate()
        now = advance(runtime, now, 0)
        name, event = events(runtime, now)[1]
        assert name == NAME and event["cleanActionSequence"] == 2 and event["finalMeasurementEventSequence"] == 0
        assert event["mcuCommandUid"] == (command["mcuCommandUid"] if grant_reopen else "77777777-7777-4777-8777-777777777777")
        assert store.get_native_measurement_event(42, final["mcuEventSequence"])["payload"] == old_raw
        if not grant_reopen:
            intent = save_intent(runtime, start, "CLEAN_UNLOCK_REQUESTED", 2, now, store)
            assert intent["cleanActionSequence"] == 2
        receipt = store.save_native_actuator_event(name, uart.encode_payload(name, event))
        exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)
        now = advance(runtime, now, 300000)
        assert not confirm(runtime, clean, start, final, now)
        assert events(runtime, now)[0][1]["status"] == "NOT_FOUND"  # No second/relabelled expiry record.
        assert work(runtime, start, now)["phase"] == "CLEAN_RECOVERY_REQUIRED"
    finally:
        store.close()


def test_pinch_alone_is_not_clean_interruption_or_physical_close_confirmation(runtime, tmp_path):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    runtime[0].TestFacts_Pinch(1)
    now = advance(runtime, now, 1000)
    assert events(runtime, now)[0][1]["status"] == "NOT_FOUND"
    assert work(runtime, start, now)["phase"] == "CLEAN_ACTIVE"
    assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)


def test_update_between_fault_poll_and_pulse_retirement_cannot_lose_interruption_cause(runtime, tmp_path):
    clean = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    assert lib.ActuatorRuntime_SetDoorTarget(1)
    start, initial, now = prepared(runtime, tmp_path, clean=True)
    name, command = grant(start, initial, clean=True)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    now = advance(runtime, now, 200, poll=False)
    # Stop after the interruption detector's two reads but before pulse retirement.
    lib.TestFacts_StopOnEntry(3)
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    now = advance(runtime, now, 100)
    cursor, records = 0, []
    while True:
        frames = exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=100 + len(records), targetMcuBootId=42,
            afterMcuEventSequence=cursor), now=now)
        if frames[0][1]["status"] != "HELD":
            break
        records.append(frames[1])
        cursor = frames[1][1]["mcuEventSequence"]
    assert [name for name, _ in records] == ["CLEAN_LOCK_POWER_CHANGED", "CLEAN_LOCK_POWER_CHANGED", NAME]
    assert records[-1][1]["interruptedPhase"] == "CLEAN_UNLOCK_PULSE"
    assert records[-1][1]["interruptionReason"] == "UPDATE_STOPPED"
    assert records[-1][1]["uptimeMs"] == now - 100
    assert work(runtime, start, now)["phase"] == "CLEAN_RECOVERY_REQUIRED" and clean
