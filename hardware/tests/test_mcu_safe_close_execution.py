"""Native recovery close through actual C bytes/timer and real Pi event custody."""
import ctypes as c
import pytest

import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange
from hardware.tests.test_mcu_delivery_execution import facts, events


def command(**changes):
    value = dict(targetMcuBootId=42, commandSequence=1,
        mcuCommandUid="77777777-7777-4777-8777-777777777777",
        scope="SINGLE_DELIVERY_DOOR", portNo=1, executionDeadlineMs=1000)
    value.update(changes)
    value["commandDigestSha256"] = uart.compute_command_digest("SAFE_CLOSE", value)
    return value


def enable(runtime, *, bind=True):
    lib, endpoint, preparation, *_ = runtime
    owner = (c.c_uint64 * 32)()
    assert lib.McuSafeCloseExecution_Attach(owner, preparation, endpoint)
    if bind:
        exchange(runtime, "BOOT_PROBE", dict(probeId=1))
        assert exchange(runtime, "BIND_BOOT", dict(probeId=1, proposedMcuBootId=42))[0][1]["status"] == "BOUND"
    return owner


def advance(runtime, now, amount, *, foreground=True):
    lib, endpoint, preparation, *_ = runtime
    lib.RuntimeClock_Advance(amount)
    lib.ActuatorRuntime_Tick()
    if foreground:
        lib.McuWorkPreparation_Poll(preparation, endpoint, now + amount)
    return now + amount


def test_fresh_bound_recovery_closes_once_and_hands_off_actual_output(runtime, tmp_path):
    owner = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    request = command()
    assert exchange(runtime, "SAFE_CLOSE", request)[0][1]["outcome"] == "ACCEPTED"
    assert events(runtime, 0)[0][1]["status"] == "NOT_FOUND"
    now = advance(runtime, 0, 99)
    assert events(runtime, now)[0][1]["status"] == "NOT_FOUND"
    now = advance(runtime, now, 1, foreground=False)
    assert facts(runtime, now)["lastDeliveryDoorCommand"] == "CLOSE"
    lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    name, output = events(runtime, now)[1]
    assert name == "SAFE_CLOSE_RESULT"
    assert output["mcuCommandUid"] == request["mcuCommandUid"]
    assert output["command"] == "CLOSE" and output["outputStatus"] == "COMMAND_DISPATCHED"
    assert output["uptimeMs"] == 100 and output["physicalDoorStateBasis"] == "NOT_OBSERVABLE"
    assert exchange(runtime, "SAFE_CLOSE", request, now=now)[0][1]["outcome"] == "ACCEPTED"
    assert events(runtime, now)[1][1] == output
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        raw = uart.encode_payload(name, output)
        receipt = store.save_native_actuator_event(name, raw)
        assert exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        assert store.get_native_actuator_event(42, output["mcuEventSequence"])["payload"] == raw
        assert store.list_pending_events() == []
        assert facts(runtime, now)["lastDeliveryDoorCommand"] == "CLOSE"
    finally:
        store.close()
    assert owner


def test_crc_valid_close_with_wrong_content_digest_never_reaches_output(runtime):
    owner = enable(runtime)
    lib, endpoint, _, replies, *_ = runtime
    raw = bytearray(uart.encode_payload("SAFE_CLOSE", command()))
    raw[-1] ^= 1  # positive but changed deadline, original digest retained
    frame = uart.encode_frame("SAFE_CLOSE", 1, raw)
    replies.clear()
    lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), 0)
    assert replies == []
    advance(runtime, 0, 100)
    assert not facts(runtime, 100)["pb7Output"]
    assert events(runtime, 100)[0][1]["status"] == "NOT_FOUND"
    assert owner


def test_pinch_pauses_then_resumes_the_same_close_without_another_command(runtime):
    owner = enable(runtime)
    lib, *_ = runtime
    lib.TestFacts_Pinch(1)
    assert exchange(runtime, "SAFE_CLOSE", command())[0][1]["outcome"] == "ACCEPTED"
    now = advance(runtime, 0, 100)
    paused = facts(runtime, now)
    assert paused["lastDeliveryDoorCommand"] == "CLOSE" and paused["pinchPaused"]
    assert not paused["pb6Output"] and not paused["pb7Output"]
    original = events(runtime, now)[1][1]
    assert original["outputStatus"] == "COMMAND_DISPATCHED"
    lib.TestFacts_Pinch(0)
    now = advance(runtime, now, 1)
    assert facts(runtime, now)["pb7Output"] and not facts(runtime, now)["pinchPaused"]
    assert events(runtime, now)[1][1] == original
    assert owner


@pytest.mark.parametrize("point", ["before_acceptance", "before_dispatch", "after_dispatch"])
def test_update_stop_cannot_resume_or_rewrite_an_already_dispatched_close(runtime, point):
    owner = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    if point == "before_acceptance":
        lib.ActuatorRuntime_StopForUpdate()
    reply = exchange(runtime, "SAFE_CLOSE", command())[0][1]
    now = 0
    if point == "before_acceptance":
        assert reply["outcome"] == "REJECTED" and reply["errorCode"] == "SAFETY_BLOCKED"
    else:
        assert reply["outcome"] == "ACCEPTED"
        now = advance(runtime, 0, 100 if point == "after_dispatch" else 50, foreground=False)
        lib.ActuatorRuntime_StopForUpdate()
        lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        event = events(runtime, now)[1][1]
        assert event["outputStatus"] == ("COMMAND_DISPATCHED" if point == "after_dispatch" else "OUTPUT_REJECTED")
    now = advance(runtime, now, 1000)
    snapshot = facts(runtime, now)
    assert snapshot["updateLatched"] and not snapshot["pb6Output"] and not snapshot["pb7Output"]
    if point != "before_acceptance":
        assert events(runtime, now)[1][1] == event
    assert owner


def test_expired_delayed_timer_retains_rejected_attempt_not_fake_close(runtime):
    owner = enable(runtime)
    assert exchange(runtime, "SAFE_CLOSE", command(executionDeadlineMs=101))[0][1]["outcome"] == "ACCEPTED"
    advance(runtime, 0, 101)
    event = events(runtime, 101)[1][1]
    assert event["outputStatus"] == "OUTPUT_REJECTED" and event["uptimeMs"] == 101
    assert not facts(runtime, 101)["pb7Output"]
    assert owner


@pytest.mark.parametrize("case,expected", [("port", "INVALID_FIELD"), ("all", "INVALID_FIELD"),
    ("deadline", "EXPIRED"), ("capacity", "BUSY"), ("sequence_capacity", "BUSY"),
    ("guard", "SAFETY_BLOCKED"), ("lock", "BUSY")])
def test_rejected_close_never_changes_outputs_or_creates_an_action_record(runtime, case, expected):
    from hardware.tests.test_mcu_actuator_event_journal import Reservation
    owner = enable(runtime)
    lib, endpoint, _, _, prerequisites, *_ = runtime
    fields = {}
    if case == "port": fields["portNo"] = 2
    if case == "all": fields.update(scope="ALL_DELIVERY_DOORS", portNo=0)
    if case == "deadline": fields["executionDeadlineMs"] = 100
    if case == "guard": prerequisites["error"] = 11
    if case == "sequence_capacity": lib.TestPreparation_SetEventSequence(endpoint, 0xffffffff)
    if case == "capacity":
        reservation = Reservation()
        assert lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, 8, c.byref(reservation))
    if case == "lock":
        assert lib.ActuatorRuntime_Unlock(1000)
    before = facts(runtime, 0)
    reply = exchange(runtime, "SAFE_CLOSE", command(**fields))[0][1]
    assert reply["outcome"] == "REJECTED" and reply["errorCode"] == expected
    after = facts(runtime, 0)
    for key in ("pb6Output", "pb7Output", "cleanLockPowered", "lastDeliveryDoorCommand"):
        assert after[key] == before[key]
    assert events(runtime, 0)[0][1]["status"] == "NOT_FOUND"
    assert owner


def test_saved_close_can_coalesce_new_close_without_switching_outputs_off(runtime, tmp_path):
    owner = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    assert exchange(runtime, "SAFE_CLOSE", command())[0][1]["outcome"] == "ACCEPTED"
    now = advance(runtime, 0, 100)
    name, event = events(runtime, now)[1]
    next_request = command(commandSequence=2, mcuCommandUid="88888888-8888-4888-8888-888888888888")
    reply = exchange(runtime, "SAFE_CLOSE", next_request, now=now)[0][1]
    assert reply["outcome"] == "REJECTED" and reply["errorCode"] == "BUSY"
    with_store = EdgeStore(str(tmp_path / "close.db"))
    with_store.initialize()
    try:
        receipt = with_store.save_native_actuator_event(name, uart.encode_payload(name, event))
        assert exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        # The rejected command stays rejected; only a new identity can be considered.
        assert exchange(runtime, "SAFE_CLOSE", next_request, now=now)[0][1]["outcome"] == "REJECTED"
        last = command(commandSequence=3, mcuCommandUid="99999999-9999-4999-8999-999999999999", executionDeadlineMs=1)
        assert exchange(runtime, "SAFE_CLOSE", last, now=now)[0][1]["outcome"] == "ACCEPTED"
        assert facts(runtime, now)["pb7Output"]
        lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        result = events(runtime, now)[1][1]
        assert result["outputStatus"] == "COALESCED_WITH_EXISTING_CLOSE" and result["uptimeMs"] == now
        assert result["mcuCommandUid"] == last["mcuCommandUid"]
        assert with_store.get_native_actuator_event(42, event["mcuEventSequence"])["payload"] == uart.encode_payload(name, event)
    finally:
        with_store.close()
    assert owner


def test_unattached_endpoint_and_new_boot_never_execute_old_close(runtime):
    lib, endpoint, preparation, replies, _, sink, guard = runtime
    assert exchange(runtime, "SAFE_CLOSE", command()) == []
    owner = enable(runtime)
    assert exchange(runtime, "SAFE_CLOSE", command())[0][1]["outcome"] == "ACCEPTED"
    advance(runtime, 0, 100)
    lib.TestFacts_InitHardware()
    lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
    assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
    assert lib.McuSafeCloseExecution_Attach(owner, preparation, endpoint)
    exchange(runtime, "BOOT_PROBE", dict(probeId=2))
    exchange(runtime, "BIND_BOOT", dict(probeId=2, proposedMcuBootId=43))
    assert exchange(runtime, "SAFE_CLOSE", command())[0][1]["outcome"] == "BOOT_MISMATCH"
    advance(runtime, 0, 200)
    observation = exchange(runtime, "QUERY_DEVICE_FACTS", dict(queryId=3, targetMcuBootId=43, portNo=1), now=200)[0][1]
    assert not observation["pb7Output"] and not observation["doorActionActive"]
    assert owner


@pytest.mark.parametrize("clean", [False, True], ids=["delivery", "clean"])
def test_standalone_recovery_does_not_preempt_original_running_work(runtime, tmp_path, clean):
    from hardware.tests.test_mcu_opening_gate import prepared
    owner = enable(runtime, bind=False)
    start, _, now = prepared(runtime, tmp_path, clean=clean)
    from hardware.tests.test_mcu_work_preparation import original_scope
    before = exchange(runtime, "QUERY_WORK", original_scope(start, clean=clean), now=now)[0][1]
    result = exchange(runtime, "SAFE_CLOSE", command(commandSequence=8), now=now)[0][1]
    assert result["outcome"] == "REJECTED" and result["errorCode"] == "BUSY"
    assert exchange(runtime, "QUERY_WORK", original_scope(start, clean=clean), now=now)[0][1] == before
    assert events(runtime, now)[0][1]["status"] == "NOT_FOUND"
    assert owner


def test_recovery_custody_blocks_configuration_until_exact_save(runtime, tmp_path):
    from mcu_configuration import NativeMcuConfiguration
    from hardware.tests.test_native_configuration import inputs
    owner = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    assert exchange(runtime, "SAFE_CLOSE", command())[0][1]["outcome"] == "ACCEPTED"
    now = advance(runtime, 0, 100)
    configuration = NativeMcuConfiguration(**inputs())
    def begin(sequence):
        name, raw = configuration.encode_part(1, application_uid="11111111-1111-4111-8111-111111111111",
            mcu_command_uid=f"00000000-0000-4000-8000-{sequence:012d}", target_mcu_boot_id=42, command_sequence=sequence)
        return exchange(runtime, name, payload=raw, now=now)[0][1]
    assert begin(2)["errorCode"] == "BUSY"
    name, event = events(runtime, now)[1]
    store = EdgeStore(str(tmp_path / "config.db"))
    store.initialize()
    try:
        receipt = store.save_native_actuator_event(name, uart.encode_payload(name, event))
        assert exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        assert begin(3)["outcome"] == "ACCEPTED"
        assert facts(runtime, now)["pb7Output"]
    finally:
        store.close()
    assert owner


@pytest.mark.parametrize("fault", ["save_failure", "saved_request_lost", "saved_reply_lost"])
def test_actual_close_custody_survives_pi_restart_without_repeating_motion(runtime, tmp_path, fault):
    import sqlite3
    from mcu_actuator_handoff import McuActuatorEventHandoff
    owner = enable(runtime)
    lib, endpoint, preparation, replies, *_ = runtime
    assert exchange(runtime, "SAFE_CLOSE", command())[0][1]["outcome"] == "ACCEPTED"
    now = advance(runtime, 0, 100)
    output = events(runtime, now)[1][1]
    expected = uart.encode_payload("SAFE_CLOSE_RESULT", output)
    replies.clear()
    store = EdgeStore(str(tmp_path / "custody.db"))
    store.initialize()
    sent, lose = [], {"enabled": True}
    def write(frame):
        decoded = uart.decode_frame(frame)
        sent.append(decoded["messageName"])
        if decoded["messageName"] == "ACTUATOR_EVENT_SAVED":
            assert store.get_native_actuator_event(42, output["mcuEventSequence"])["payload"] == expected
            if lose["enabled"] and fault == "saved_request_lost": return len(frame)
        lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), now)
        if lose["enabled"] and fault == "saved_reply_lost" and decoded["messageName"] == "ACTUATOR_EVENT_SAVED": replies.clear()
        return len(frame)
    try:
        client = McuActuatorEventHandoff(store, write, 42)
        client.poll(now)
        if fault == "save_failure":
            store._conn.set_authorizer(lambda operation, table, *args:
                sqlite3.SQLITE_DENY if operation == sqlite3.SQLITE_INSERT and table == "native_actuator_event" else sqlite3.SQLITE_OK)
            with pytest.raises(sqlite3.DatabaseError):
                while replies: client.accept_frame(replies.pop(0), now)
            assert sent == ["QUERY_ACTUATOR_EVENT"]
            store._conn.set_authorizer(None)
        else:
            while replies: client.accept_frame(replies.pop(0), now)
        store.close()
        store = EdgeStore(str(tmp_path / "custody.db"))
        store.initialize()
        lose["enabled"] = False
        client = McuActuatorEventHandoff(store, write, 42)
        now = advance(runtime, now, 1000)
        client.poll(now)
        while replies: client.accept_frame(replies.pop(0), now)
        lib.McuWorkPreparation_Poll(preparation, endpoint, now)
        assert store.get_native_actuator_event(42, output["mcuEventSequence"])["payload"] == expected
        assert events(runtime, now)[0][1]["status"] == "NOT_FOUND"
        assert "SAFE_CLOSE" not in sent
        assert store.list_pending_events() == []
        assert facts(runtime, now)["pb7Output"]
    finally:
        store.close()
    assert owner


@pytest.mark.parametrize("preemption", ["deadline", "update"])
def test_preemption_after_acceptance_before_output_keeps_rejected_execution_fact(runtime, preemption):
    owner = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    # Real hardware critical-section boundary: snapshot, delivery, pulse, Begin.
    if preemption == "deadline": lib.TestFacts_AdvanceOnEntry(4, 1001)
    else: lib.TestFacts_StopOnEntry(4)
    reply = exchange(runtime, "SAFE_CLOSE", command())[0][1]
    assert reply["outcome"] == "ACCEPTED"  # acceptance cannot be rewritten after output attempt
    now = 1001 if preemption == "deadline" else 0
    lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    event = events(runtime, now)[1][1]
    assert event["outputStatus"] == "OUTPUT_REJECTED" and event["uptimeMs"] == now
    assert not facts(runtime, now)["pb7Output"]
    assert owner
