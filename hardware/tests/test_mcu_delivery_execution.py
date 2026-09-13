"""START-owned delivery behavior plus temporary legacy-fixture compatibility."""
import ctypes as c

import pytest
import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_opening_gate import prepared, grant
from hardware.tests.test_mcu_work_preparation import exchange, original_scope, take_samples
from hardware.tests.test_mcu_simplified_execution import (
    final as autonomous_final,
    library,
    runtime,
    select as autonomous_select,
    setup as autonomous_setup,
    state as autonomous_state,
    tick as autonomous_tick,
)
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_mcu_actuator_event_journal import Reservation


def enable(runtime):
    lib, endpoint, preparation, *_ = runtime
    execution = (c.c_uint64 * 64)()
    assert lib.McuDeliveryExecution_Attach(execution, preparation, endpoint)
    return execution


def events(runtime, now):
    return exchange(runtime, "QUERY_ACTUATOR_EVENT", dict(queryId=100, targetMcuBootId=42,
        afterMcuEventSequence=0), now=now)


def test_start_autonomously_opens_closes_measures_and_builds_one_final_result(runtime):
    execution, _, start, now = autonomous_setup(runtime)

    now = autonomous_tick(runtime, now, 100)
    opened = facts(runtime, now)
    assert opened["pb6Output"] and not opened["pb7Output"]
    assert opened["lastDeliveryDoorCommand"] == "OPEN"

    now = autonomous_tick(runtime, now, start["deliveryAutoCloseMs"])
    reversing = facts(runtime, now)
    assert not reversing["pb6Output"] and not reversing["pb7Output"]
    assert reversing["lastDeliveryDoorCommand"] == "OPEN"

    now = autonomous_tick(runtime, now, 100)
    closing = facts(runtime, now)
    assert not closing["pb6Output"] and closing["pb7Output"]
    assert closing["lastDeliveryDoorCommand"] == "CLOSE"
    assert autonomous_state(runtime, start, now)["phase"] == "DELIVERY_CLOSE_TRAVEL_WAIT"

    now = autonomous_tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    now = take_samples(runtime, [1200] * 5, start=now, measurement=2)
    assert autonomous_select(runtime, execution, now)
    result = autonomous_final(runtime, start, now)
    assert result["initialWeightGrams"] == 500
    assert result["finalWeightGrams"] == 1200
    assert result["deliveryRoundCount"] == 1
    assert result["finishReason"] == "DELIVERY_END"


def facts(runtime, now):
    return exchange(runtime, "QUERY_DEVICE_FACTS", dict(queryId=101, targetMcuBootId=42, portNo=1), now=now)[0][1]


def test_pinch_only_pauses_autonomous_close_and_release_resumes_without_a_new_edge_command(runtime):
    execution, _, start, now = autonomous_setup(runtime)
    lib = runtime[0]
    lib.TestFacts_Pinch(1)

    now = autonomous_tick(runtime, now, 100)
    opened = facts(runtime, now)
    assert opened["pb6Output"] and opened["pb5Active"] and not opened["pinchPaused"]

    now = autonomous_tick(runtime, now, start["deliveryAutoCloseMs"])
    reversing = facts(runtime, now)
    assert not reversing["pb6Output"] and not reversing["pb7Output"]
    assert reversing["lastDeliveryDoorCommand"] == "OPEN"

    now = autonomous_tick(runtime, now, 100)
    paused = facts(runtime, now)
    assert paused["lastDeliveryDoorCommand"] == "CLOSE" and paused["pinchPaused"]
    assert not paused["pb6Output"] and not paused["pb7Output"]

    lib.TestFacts_Pinch(0)
    lib.ActuatorRuntime_Tick()
    assert facts(runtime, now)["pb7Output"]

    now = autonomous_tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    now = take_samples(runtime, [700] * 5, start=now, measurement=2)
    assert autonomous_select(runtime, execution, now)
    assert autonomous_final(runtime, start, now)["finishReason"] == "DELIVERY_END"


@pytest.mark.parametrize("opened", [False, True])
def test_update_cancels_autonomous_delivery_and_pinch_release_cannot_revive_it(runtime, opened):
    _, _, start, now = autonomous_setup(runtime)
    lib = runtime[0]
    if opened:
        now = autonomous_tick(runtime, now, 100)
        assert facts(runtime, now)["pb6Output"]

    lib.ActuatorRuntime_StopForUpdate()
    lib.TestFacts_Pinch(0)
    now = autonomous_tick(runtime, now, start["deliveryAutoCloseMs"] + 200)
    observed = facts(runtime, now)
    assert observed["updateLatched"] and not observed["doorActionActive"]
    assert not observed["pb6Output"] and not observed["pb7Output"]
    assert observed["retainedWorkPhase"] == "DELIVERY_FINALIZING"
    result = autonomous_final(runtime, start, now)
    assert result["finishReason"] == "CANCELLED"
    assert result["finalKind"] == "NOT_TAKEN"


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
