"""Native first-open wire command -> actual C timer -> immutable action custody."""
import ctypes as c

import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_opening_gate import prepared, grant
from hardware.tests.test_mcu_work_preparation import library, runtime, exchange, original_scope
from hardware.tests.test_mcu_actuator_event_journal import Reservation


def enable(runtime):
    lib, endpoint, preparation, *_ = runtime
    execution = (c.c_uint64 * 64)()
    assert lib.McuDeliveryExecution_Attach(execution, preparation, endpoint)
    return execution


def events(runtime, now):
    return exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=100, targetMcuBootId=42,
        afterMcuEventSequence=0), now=now)


def test_first_open_is_accepted_once_then_auto_closes_without_waiting_for_evidence_save(runtime, tmp_path):
    execution = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    start, initial, now = prepared(runtime, tmp_path)
    name, command = grant(start, initial)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    assert events(runtime, now)[0][1]["status"] == "NOT_FOUND"
    lib.RuntimeClock_Advance(100)
    lib.ActuatorRuntime_Tick()
    now += 100
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    opened = events(runtime, now)[1][1]
    assert opened["command"] == "OPEN" and opened["outputStatus"] == "COMMAND_DISPATCHED"
    assert opened["mcuCommandUid"] == command["mcuCommandUid"]
    assert opened["sessionUid"] == start["sessionUid"] and opened["uptimeMs"] == now
    # No save or foreground polling until after the timer has closed the cycle.
    lib.RuntimeClock_Advance(start["deliveryAutoCloseMs"])
    lib.ActuatorRuntime_Tick()
    lib.RuntimeClock_Advance(100)
    lib.ActuatorRuntime_Tick()
    now += start["deliveryAutoCloseMs"] + 100
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    assert events(runtime, now)[1][1] == opened
    store = EdgeStore(str(tmp_path / "actions.db"))
    store.initialize()
    try:
        receipt = store.save_native_actuator_event("DELIVERY_DOOR_COMMAND_RESULT",
            uart.encode_payload("DELIVERY_DOOR_COMMAND_RESULT", opened))
        assert exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
        closed = events(runtime, now)[1][1]
        assert closed["command"] == "CLOSE" and closed["outputStatus"] == "COMMAND_DISPATCHED"
        assert closed["mcuCommandUid"] == command["mcuCommandUid"]
        assert closed["mcuEventSequence"] == opened["mcuEventSequence"] + 1
        assert closed["uptimeMs"] == now
        assert store.get_native_actuator_event(42, opened["mcuEventSequence"])["payload"]
        assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
        assert events(runtime, now)[1][1] == closed
        assert exchange(runtime, "QUERY_WORK", original_scope(start), now=now)[0][1]["status"] == "RUNNING"
        assert store.list_native_result_report_tasks() == []
    finally:
        store.close()
    assert execution  # Own C application storage through the entire exchange.


def facts(runtime, now):
    return exchange(runtime, "QUERY_DEVICE_FACTS", dict(queryId=101, targetMcuBootId=42, portNo=1), now=now)[0][1]


@pytest.mark.parametrize("saved,remaining,guard_error,reserved,expected", [
    (False, 10000, 0, 0, "BUSY"),
    (True, 100, 0, 0, "EXPIRED"),
    (True, 10000, 5, 0, "INVALID_FIELD"),
    (True, 10000, 0, 7, "BUSY"),
    (True, 10000, 0, 6, "BUSY"),  # OPEN/CLOSE fit, but no interruption promise.
])
def test_rejected_first_open_consumes_decision_but_never_starts_or_leaks_partial_reservation(
        runtime, tmp_path, saved, remaining, guard_error, reserved, expected):
    execution = enable(runtime)
    lib, endpoint, preparation, _, prerequisites, *_ = runtime
    start, initial, now = prepared(runtime, tmp_path, saved=saved)
    token = Reservation()
    if reserved:
        assert lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, reserved, c.byref(token))
    prerequisites["error"] = guard_error
    name, command = grant(start, initial, remainingStartAuthorizationMs=remaining)
    response = exchange(runtime, name, command, now=now)[0][1]
    assert response["outcome"] == "REJECTED" and response["errorCode"] == expected
    prerequisites["error"] = 0
    if reserved:
        assert lib.McuControlEndpoint_CancelActuatorEvents(endpoint, c.byref(token))
    assert lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, 8, c.byref(token))
    assert lib.McuControlEndpoint_CancelActuatorEvents(endpoint, c.byref(token))
    lib.RuntimeClock_Advance(200)
    lib.ActuatorRuntime_Tick()
    now += 200
    assert exchange(runtime, name, command, now=now)[0][1] == response
    assert not facts(runtime, now)["pb6Output"]
    assert events(runtime, now)[0][1]["status"] == "NOT_FOUND"
    assert execution and preparation


def test_authorization_expiring_before_delayed_timer_dispatch_retains_rejection_without_fake_close(runtime, tmp_path):
    execution = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    start, initial, now = prepared(runtime, tmp_path)
    name, command = grant(start, initial, remainingStartAuthorizationMs=101)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    lib.RuntimeClock_Advance(101)
    lib.ActuatorRuntime_Tick()
    now += 101
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    rejected = events(runtime, now)[1][1]
    assert rejected["command"] == "OPEN" and rejected["outputStatus"] == "OUTPUT_REJECTED"
    assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"]
    reservation = Reservation()
    # Rejected OPEN and its explicit abort each retain one evidence slot.
    assert not lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, 7, c.byref(reservation))
    assert lib.McuControlEndpoint_ReserveActuatorEvents(endpoint, 6, c.byref(reservation))
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    assert events(runtime, now)[1][1] == rejected
    assert execution


def test_pinch_only_pauses_close_and_release_resumes_without_new_command(runtime, tmp_path):
    execution = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    start, initial, now = prepared(runtime, tmp_path)
    lib.TestFacts_Pinch(1)
    name, command = grant(start, initial)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    lib.RuntimeClock_Advance(100)
    lib.ActuatorRuntime_Tick()
    now += 100
    opened = facts(runtime, now)
    assert opened["pb6Output"] and opened["pb5Active"] and not opened["pinchPaused"]
    lib.RuntimeClock_Advance(start["deliveryAutoCloseMs"])
    lib.ActuatorRuntime_Tick()
    now += start["deliveryAutoCloseMs"]
    waiting = facts(runtime, now)
    assert not waiting["pb6Output"] and not waiting["pb7Output"]
    assert not waiting["doorActionActive"] and waiting["lastDeliveryDoorCommand"] == "OPEN"
    lib.RuntimeClock_Advance(100)
    lib.ActuatorRuntime_Tick()
    now += 100
    paused = facts(runtime, now)
    assert paused["lastDeliveryDoorCommand"] == "CLOSE" and paused["pinchPaused"]
    assert not paused["pb6Output"] and not paused["pb7Output"]
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    lib.TestFacts_Pinch(0)
    lib.ActuatorRuntime_Tick()
    assert facts(runtime, now)["pb7Output"]
    assert execution


@pytest.mark.parametrize("opened", [False, True])
def test_update_cancels_pending_cycle_and_pinch_release_cannot_revive_it(runtime, tmp_path, opened):
    execution = enable(runtime)
    lib, endpoint, preparation, *_ = runtime
    start, initial, now = prepared(runtime, tmp_path)
    name, command = grant(start, initial)
    assert exchange(runtime, name, command, now=now)[0][1]["outcome"] == "ACCEPTED"
    if opened:
        lib.RuntimeClock_Advance(100)
        lib.ActuatorRuntime_Tick()
        now += 100
    lib.ActuatorRuntime_StopForUpdate()
    lib.TestFacts_Pinch(0)
    lib.RuntimeClock_Advance(start["deliveryAutoCloseMs"] + 200)
    lib.ActuatorRuntime_Tick()
    now += start["deliveryAutoCloseMs"] + 200
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    state = facts(runtime, now)
    assert state["updateLatched"] and not state["doorActionActive"]
    assert not state["pb6Output"] and not state["pb7Output"]
    event = events(runtime, now)[1][1]
    assert event["command"] == "OPEN"
    assert event["outputStatus"] == ("COMMAND_DISPATCHED" if opened else "OUTPUT_REJECTED")
    assert state["retainedWorkPhase"] == "SAFETY_LOCKED"
    assert execution


from contextlib import contextmanager
from types import SimpleNamespace


@contextmanager
def executed_action_case(runtime, tmp_path, *, clean_work, lose_decision_and_restart_pi=True, cloud_command_factory=None,
                         stop_before_measurement=False):
    from mcu_session import McuBootSession, McuCommandDispatcher, NativePhysicalActionGate
    from mcu_configuration import NativeMcuConfiguration
    from mcu_process_handoff import McuProcessEventHandoff
    from mcu_actuator_handoff import McuActuatorEventHandoff
    from job_safety import PhysicalAction, action_digest
    from tests.test_command_processor import make_real_job_safety
    from tests.test_job_safety import _command
    from hardware.tests.test_native_configuration import inputs
    from hardware.tests.test_mcu_work_preparation import start_values, take_samples

    if clean_work:
        from hardware.tests.test_mcu_clean_execution import enable as enable_clean
        execution = enable_clean(runtime)
    else:
        execution = enable(runtime)
    lib, endpoint, preparation, replies, *_ = runtime
    if clean_work:
        assert lib.ActuatorRuntime_SetDoorTarget(1)
    work_type = "CLEAN" if clean_work else "DELIVERY"
    start_name = "START_CLEAN_OPERATION" if clean_work else "START_DELIVERY_SESSION"
    action_name = "UNLOCK_CLEAN_DOOR" if clean_work else "AUTHORIZE_DELIVERY_FIRST_OPEN"
    event_name = "WORK_PREUNLOCK_WEIGHT_READY" if clean_work else "WORK_PREOPEN_WEIGHT_READY"
    work_key = "operationUid" if clean_work else "sessionUid"
    action_key = "clean:first-unlock" if clean_work else "delivery:first-open"
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    updater, safety = make_real_job_safety(tmp_path)
    clock, sent = [0], []
    case = None

    def write(frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        sent.append(decoded["messageName"])
        if decoded["messageName"] == action_name:
            uid = uart.decode_payload(decoded["messageName"], decoded["payload"])["mcuCommandUid"]
            assert store.get_native_command(uid)["write_claimed"] == 1
            assert safety.get_physical_action(uid)["state"] == "ARMED"
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), clock[0]) == 1
        return len(frame)

    def pump(client):
        while replies:
            client.accept_frame(replies.pop(0), clock[0])

    def preparation_guard(record):
        assert record["message_name"] in {"CONFIG_BEGIN", "CONFIG_DEVICE_BLOCK", "CONFIG_PORT_BLOCK",
            "CONFIG_COMMIT", start_name}
        return lambda: None  # Non-actuating control authorization boundary only.

    identity = {"mcuCommandUid", "targetMcuBootId", "commandSequence", "commandDigestSha256"}
    try:
        boot = McuBootSession(store, write)
        boot.poll(0)
        pump(boot)
        assert boot.current_boot(0) == 1
        dispatcher = McuCommandDispatcher(store, boot, write, arm=preparation_guard, clock=lambda: clock[0])
        config = NativeMcuConfiguration(**inputs())
        for index in range(1, config.part_count + 1):
            name, raw = config.encode_part(index, application_uid="11111111-1111-4111-8111-111111111111",
                mcu_command_uid=f"00000000-0000-4000-8000-{index:012d}", target_mcu_boot_id=1, command_sequence=index)
            values = uart.decode_payload(name, raw)
            record = store.prepare_native_command(name, values["mcuCommandUid"], 1,
                {key: value for key, value in values.items() if key not in identity})
            assert record["payload"] == raw
            assert dispatcher.send_once(record["command_uid"])
            pump(dispatcher)
            assert store.get_native_command(record["command_uid"])["decision_outcome"] == "ACCEPTED"
        # Explicit model of the other configuration consumers; not a production
        # claim that all hardware configuration has already been applied.
        assert lib.McuDeviceFacts_PublishConfiguration(lib.TestPreparation_Facts(endpoint), values["configVersion"],
            bytes.fromhex(values["contentSha256"]), bytes.fromhex(values["mcuPayloadSha256"]), 0)
        start = start_values(start_name, targetMcuBootId=1, **{work_key: "22222222-2222-4222-8222-222222222222"})
        cloud_command = _command()
        cloud_command["commandType"] = start_name
        cloud_command["payload"] = {work_key: start[work_key], "portNo": 1}
        if cloud_command_factory is not None:
            cloud_command = cloud_command_factory(cloud_command, start, inputs())
            assert store.receive_command(cloud_command["commandUid"], start_name, cloud_command) == "ACCEPTED"
            assert store.claim_next_command()["command_uid"] == cloud_command["commandUid"]
        permit = safety.request_job(cloud_command, work_type=work_type, work_uid=start[work_key])
        safety.begin_job(permit, begin_uid="55555555-5555-4555-8555-555555555555", digest=permit.request_digest_sha256)
        assert store.acquire_work_slot(work_type, start[work_key], 1, {"phase": "NATIVE_PREPARING"})
        occupancy = store.get_work_slot()
        store.prepare_native_command(start_name, start["mcuCommandUid"], 1,
            {key: value for key, value in start.items() if key not in identity})
        assert dispatcher.send_once(start["mcuCommandUid"])
        pump(dispatcher)
        if stop_before_measurement:
            case = SimpleNamespace(store=store, safety=safety, updater=updater, permit=permit, action=None,
                occupancy=occupancy, sent=sent, action_name=action_name, start=start,
                clean=clean_work, execution=execution, now=clock[0])
            yield case
            return
        clock[0] = take_samples(runtime, [500] * 5)
        scope = original_scope(start, clean=clean_work) | dict(eventMessageType=event_name, stepSequence=0 if clean_work else 1,
            configVersion=start["configVersion"])
        del scope["queryId"]
        measurement = McuProcessEventHandoff(store, write, scope)
        measurement.poll(clock[0])
        pump(measurement)
        saved = store.get_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", scope | {"queryId": 1})[8:])
        initial = uart.decode_payload(saved["message_name"], saved["payload"])
        name, opening = grant(start, initial, clean=clean_work, targetMcuBootId=1)
        record = store.prepare_native_command(name, opening["mcuCommandUid"], 1,
            {key: value for key, value in opening.items() if key not in identity})
        digest = action_digest(work_uid=permit.work_uid, command_uid=permit.command_uid,
            action_key=action_key, action_kind=name, payload={"nativeUartPayloadHex": record["payload"].hex()})
        action = PhysicalAction(record["command_uid"], "66666666-6666-4666-8666-666666666666",
            action_key, name, digest)

        def validate_original(candidate):
            assert candidate["payload"] == record["payload"]
            assert store.get_work_slot() == occupancy
            assert saved["payload"] == store.get_native_process_receipt(saved["scope"])["payload"]
            # Camera/safety readiness are the explicit external guard boundary
            # in this fixture, not claimed as real photos or real hardware HIL.

        boot.poll(clock[0])
        pump(boot)
        gate = NativePhysicalActionGate(safety, permit, action, store=store, revalidate=validate_original,
            deadline_ms=clock[0] + 10000, clock=lambda: clock[0])
        dispatcher = McuCommandDispatcher(store, boot, write, arm=gate, clock=lambda: clock[0])
        assert dispatcher.send_once(record["command_uid"])
        if lose_decision_and_restart_pi:
            replies.clear()
            store.close()
            store = EdgeStore(str(tmp_path / "edge.db"))
            store.initialize()
            boot = McuBootSession(store, write)
            boot.poll(clock[0])
            pump(boot)
            assert boot.current_boot(clock[0]) == 1
            dispatcher = McuCommandDispatcher(store, boot, write,
                arm=lambda _: pytest.fail("restart must not arm the old command"), clock=lambda: clock[0])
            assert not dispatcher.send_once(record["command_uid"])
            dispatcher.poll(record["command_uid"], clock[0])
        pump(dispatcher)
        assert store.get_native_command(record["command_uid"])["decision_outcome"] == "ACCEPTED"
        for elapsed in ((opening["unlockPulseMs"],) if clean_work else (100, start["deliveryAutoCloseMs"], 100)):
            lib.RuntimeClock_Advance(elapsed)
            lib.ActuatorRuntime_Tick()
            clock[0] += elapsed
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, clock[0])
        custody = McuActuatorEventHandoff(store, write, 1)
        for _ in range(3):
            custody.poll(clock[0])
            pump(custody)
            lib.RuntimeClock_Advance(1000)
            lib.ActuatorRuntime_Tick()
            clock[0] += 1000
        states = ((2, "ENERGIZED"), (3, "DEENERGIZED")) if clean_work else ((2, "OPEN"), (3, "CLOSE"))
        for sequence, direction in states:
            event = store.get_native_actuator_event(1, sequence)
            values = uart.decode_payload(event["message_name"], event["payload"])
            assert values["mcuCommandUid"] == record["command_uid"]
            assert values["lockPowerState" if clean_work else "command"] == direction
            assert values[work_key] == start[work_key]
        assert sent.count(action_name) == 1
        assert store.get_work_slot() == occupancy and store.list_native_result_report_tasks() == []
        assert safety.get_physical_action(record["command_uid"])["state"] == "ARMED"
        assert execution
        case = SimpleNamespace(store=store, safety=safety, updater=updater, permit=permit, action=action,
            occupancy=occupancy, sent=sent, action_name=action_name, start=start, opening=opening,
            initial=initial, scope=saved["scope"], clean=clean_work, execution=execution, now=clock[0])
        yield case
    finally:
        (case.store if case else store).close()
        (case.updater if case else updater).close()


@pytest.mark.parametrize("lose_decision_and_restart_pi", [False, True])
@pytest.mark.parametrize("clean_work", [False, True])
def test_real_pi_dispatch_ledger_c_timer_and_sqlite_custody_keep_the_same_original_command(
        runtime, tmp_path, lose_decision_and_restart_pi, clean_work):
    from mcu_action_evidence import NativeActionReconciler
    with executed_action_case(runtime, tmp_path, clean_work=clean_work,
            lose_decision_and_restart_pi=lose_decision_and_restart_pi) as case:
        confirmation = NativeActionReconciler(case.store, case.safety).reconcile(case.action.action_uid)
        assert confirmation["state"] == "CONFIRMED"
        ledger = case.safety.get_physical_action(case.action.action_uid)
        assert ledger["confirmedOutcome"] == "EXECUTED"
        assert ledger["receiptUid"] == case.action.receipt_uid
        assert ledger["evidenceDigestSha256"] == confirmation["evidence_sha256"]
        assert case.safety.get_job_permit(case.permit.permit_uid)["state"] == "ACTIVE"
        assert case.store.get_work_slot() == case.occupancy
        assert case.store.list_native_result_report_tasks() == []
        assert case.sent.count(case.action_name) == 1
