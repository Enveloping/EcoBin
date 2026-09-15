"""Autonomous work keeps weight authority separate from optional fullness diagnostics."""
import ctypes as c
import uuid

import pytest

import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_work_preparation import (
    configured,
    exchange,
    library,
    original_scope,
    runtime,
    start_values,
    take_samples,
)
from hardware.tests.test_native_configuration import inputs
from hardware.tests.test_mcu_device_facts import scale_frame


def process_scope(start, *, clean, terminal):
    if terminal:
        message = "CLEAN_FINAL_WEIGHT_READY" if clean else "WORK_POSTCLOSE_WEIGHT_READY"
        step = 1
    else:
        message = "WORK_PREUNLOCK_WEIGHT_READY" if clean else "WORK_PREOPEN_WEIGHT_READY"
        step = 0 if clean else 1
    return original_scope(start, clean=clean) | {
        "eventMessageType": message,
        "stepSequence": step,
        "configVersion": start["configVersion"],
    }


def query_process(runtime, scope, now):
    responses = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)
    return responses[0][1], responses[1][1] if len(responses) == 2 else None


def release_diagnostic(runtime, tmp_path, scope, now, suffix):
    """Release a diagnostic mailbox only so a later diagnostic can be inspected."""
    reply, event = query_process(runtime, scope, now)
    assert reply["status"] == "HELD" and event is not None
    name = scope["eventMessageType"]
    raw_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
    raw_event = uart.encode_payload(name, event)
    store = EdgeStore(str(tmp_path / f"diagnostic-{suffix}.db"))
    store.initialize()
    try:
        receipt = store.save_native_process_receipt(raw_scope, name, raw_event)
    finally:
        store.close()
    response = exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]
    assert response["status"] == "RELEASED"
    return event


def begin_work(runtime, tmp_path, *, clean=False, inspect_terminal_diagnostic=False):
    lib, endpoint, preparation, _, keepalive, *_ = runtime
    lib.TestUltrasonic_Init()
    assert lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    delivery, cleanup = (c.c_uint64 * 64)(), (c.c_uint64 * 64)()
    keepalive["autonomous_owners"] = delivery, cleanup
    assert lib.McuDeliveryExecution_Attach(delivery, preparation, endpoint)
    assert lib.McuCleanExecution_Attach(cleanup, preparation, endpoint)
    assert lib.ActuatorRuntime_SetDoorTarget(1)  # MCU boot policy: command CLOSE.
    configured(runtime, applied=True)
    name = "START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION"
    start = start_values(name)
    assert exchange(runtime, name, start)[0][1]["outcome"] == "ACCEPTED"
    now = take_samples(runtime, [500] * 5)
    assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    if inspect_terminal_diagnostic:
        release_diagnostic(runtime, tmp_path, process_scope(start, clean=clean, terminal=False),
                           now, "initial")
    return delivery, cleanup, start, now


def tick(runtime, now, duration):
    lib, endpoint, preparation, *_ = runtime
    lib.RuntimeClock_Advance(duration)
    lib.ActuatorRuntime_Tick()
    now += duration
    lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    return now


def begin_terminal_measurement(runtime, owners, start, now, *, clean=False):
    lib, endpoint, preparation, *_ = runtime
    delivery, cleanup = owners
    if clean:
        now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
        assert lib.McuCleanExecution_Request(
            cleanup, endpoint, uuid.UUID(start["operationUid"]).bytes,
            uart.MESSAGE_SPECS["CLEAN_FINISH_REQUESTED"]["id"], 0, now)
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    else:
        now = tick(runtime, now, 100)
        now = tick(runtime, now, start["deliveryAutoCloseMs"])
        now = tick(runtime, now, 100)
        now = tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    assert work_state(runtime, start, now, clean=clean)["phase"] == (
        "CLEAN_FINAL_MEASURING" if clean else "DELIVERY_POSTCLOSE_MEASURING")
    return delivery, cleanup, now


def work_state(runtime, start, now, *, clean=False):
    return exchange(runtime, "QUERY_WORK", original_scope(start, clean=clean), now=now)[0][1]


def held_result(runtime, start, now, *, clean=False):
    state = work_state(runtime, start, now, clean=clean)
    assert state["status"] == "RESULT_HELD", state
    query = {"queryId": 77, "mcuBootId": 42, "resultSequence": state["resultSequence"],
             "resultDigestSha256": state["resultDigestSha256"],
             "workUid": start["operationUid" if clean else "sessionUid"]}
    responses = exchange(runtime, "QUERY_RESULT", query, now=now)
    assert responses[0][1]["status"] == "HELD"
    return responses[1][1]


def add_weight_sample(runtime, now, measurement, value):
    lib, endpoint, preparation, *_ = runtime
    weight = lib.TestPreparation_Weight(preparation)
    attempt = lib.McuWeightRun_StartOwnedAttempt(weight, now)
    assert attempt
    lib.RuntimeClock_Advance(20)
    now += 20
    raw = scale_frame(value)
    assert lib.McuWeightRun_FinishOwnedAttempt(
        weight, measurement, attempt, now, now, raw, len(raw))
    lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    return now


def finish_weight(runtime, now, values, *, measurement=2):
    previous_started = None
    for value in values:
        if previous_started is not None:
            now = tick(runtime, now, max(0, previous_started + 250 - now))
        previous_started = now
        now = add_weight_sample(runtime, now, measurement, value)
    return now


def select_delivery(runtime, delivery, measurement_uid, now, selection="END"):
    return runtime[0].McuDeliveryExecution_Select(
        delivery, runtime[1], uuid.UUID(measurement_uid).bytes,
        uart.REGISTRY["enums"]["DeliverySelection"]["values"][selection], now)


def device_facts(runtime, now, query_id=91):
    return exchange(runtime, "QUERY_DEVICE_FACTS", {
        "queryId": query_id, "targetMcuBootId": 42, "portNo": 1}, now=now)[0][1]


def assert_no_business_fullness(event):
    """The fixed wire tail remains zero for compatibility, never a sample."""
    assert event["workFullnessStatus"] == "NOT_SAMPLED"
    assert event["fullnessGroupSequence"] == 0
    assert event["workFullnessSensorValue"] == "NOT_OBSERVED"
    assert event["fullnessRequestedSampleCount"] == 0
    assert event["fullnessCompletedSampleCount"] == 0
    assert event["fullnessValidSampleCount"] == 0
    assert not event["fullnessDistancePresent"]


def test_default_optional_source_never_delays_or_changes_autonomous_result(runtime, tmp_path):
    delivery, cleanup, start, now = begin_work(runtime, tmp_path)
    _, _, now = begin_terminal_measurement(runtime, (delivery, cleanup), start, now)
    now = take_samples(runtime, [1000] * 5, start=now, measurement=2)
    assert work_state(runtime, start, now)["phase"] == "DELIVERY_WAIT_SELECTION"
    now = tick(runtime, now, start["continueDeliveryWaitMs"])
    result = held_result(runtime, start, now)
    assert result["finishReason"] == "DELIVERY_WINDOW_EXPIRED"
    assert (result["initialWeightGrams"], result["finalWeightGrams"]) == (500, 1000)
    assert not any(key.startswith("fullness") or key.startswith("workFullness") for key in result)
    reply, first = query_process(runtime, process_scope(start, clean=False, terminal=False), now)
    assert reply["status"] == "HELD" and first["reportedWeightGrams"] == 500
    original = uart.encode_payload("WORK_RESULT", result)
    now = tick(runtime, now, 10000)
    assert uart.encode_payload("WORK_RESULT", held_result(runtime, start, now)) == original
    assert device_facts(runtime, now)["fullnessReadStatus"] == "NOT_OBSERVED"


@pytest.mark.parametrize("clean", [False, True])
def test_terminal_business_weight_never_starts_or_waits_for_ultrasonic(
        runtime, tmp_path, clean):
    delivery, cleanup, start, now = begin_work(
        runtime, tmp_path, clean=clean, inspect_terminal_diagnostic=True)
    _, _, now = begin_terminal_measurement(runtime, (delivery, cleanup), start, now, clean=clean)
    assert not runtime[0].TestUltrasonic_Trigger()
    now = finish_weight(runtime, now, [1000] * 5)
    scope = process_scope(start, clean=clean, terminal=True)
    reply, event = query_process(runtime, scope, now)
    assert reply["status"] == "HELD"
    assert event["reportedWeightGrams"] == 1000
    assert_no_business_fullness(event)
    if clean:
        result = held_result(runtime, start, now, clean=True)
        assert result["finishReason"] == "CLEAN_CONFIRMED"
        assert result["physicalCloseConfirmed"]
    else:
        assert select_delivery(runtime, delivery, event["measurementUid"], now)
        result = held_result(runtime, start, now)
        assert result["finishReason"] == "DELIVERY_END"
    assert result["finalWeightGrams"] == 1000
    assert not any(key.startswith("fullness") or key.startswith("workFullness") for key in result)
    assert device_facts(runtime, now)["fullnessReadStatus"] == "NOT_OBSERVED"


@pytest.mark.parametrize("cause", ["update", "context", "weight_timeout"])
def test_interruption_stops_weight_without_starting_or_changing_distance_fact(
        runtime, tmp_path, cause):
    delivery, cleanup, start, began = begin_work(
        runtime, tmp_path, inspect_terminal_diagnostic=True)
    _, _, now = begin_terminal_measurement(runtime, (delivery, cleanup), start, began)
    measurement_began = now
    assert not runtime[0].TestUltrasonic_Trigger()
    now = add_weight_sample(runtime, now, 2, 900)
    if cause == "update":
        runtime[0].ActuatorRuntime_StopForUpdate()
    elif cause == "context":
        runtime[0].TestFacts_ReinitializeActuator()
    else:
        now = tick(runtime, now, measurement_began + 5000 - now)
    runtime[0].McuWorkPreparation_Poll(runtime[2], runtime[1], now)
    result = held_result(runtime, start, now)
    assert result["finishReason"] == ("FAILED" if cause == "weight_timeout" else "CANCELLED")
    assert result["finalKind"] == ("UNAVAILABLE" if cause == "weight_timeout" else "INTERRUPTED")
    assert result["finalSampleCount"] == 1 and result["finalWeightGrams"] == 0
    reply, event = query_process(runtime, process_scope(start, clean=False, terminal=True), now)
    assert reply["status"] == "HELD"
    assert_no_business_fullness(event)
    assert device_facts(runtime, now)["fullnessReadStatus"] == "NOT_OBSERVED"


def test_continued_delivery_uses_new_weight_measurement_without_distance_groups(runtime, tmp_path):
    delivery, cleanup, start, now = begin_work(
        runtime, tmp_path, inspect_terminal_diagnostic=True)
    _, _, now = begin_terminal_measurement(runtime, (delivery, cleanup), start, now)
    now = finish_weight(runtime, now, [900] * 5)
    scope = process_scope(start, clean=False, terminal=True)
    _, first = query_process(runtime, scope, now)
    assert select_delivery(runtime, delivery, first["measurementUid"], now, "CONTINUE")
    release_diagnostic(runtime, tmp_path, scope, now, "round-1")
    now = tick(runtime, now, 100)
    now = tick(runtime, now, start["deliveryAutoCloseMs"])
    now = tick(runtime, now, 100)
    now = tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    assert work_state(runtime, start, now)["phase"] == "DELIVERY_POSTCLOSE_MEASURING"
    assert not runtime[0].TestUltrasonic_Trigger()
    now = finish_weight(runtime, now, [1400] * 5, measurement=3)
    second_scope = scope | {"stepSequence": 2}
    _, second = query_process(runtime, second_scope, now)
    assert second["roundIndex"] == 2
    assert second["measurementUid"] != first["measurementUid"]
    assert second["reportedWeightGrams"] == 1400
    assert_no_business_fullness(second)
    assert select_delivery(runtime, delivery, second["measurementUid"], now)
    result = held_result(runtime, start, now)
    assert result["deliveryRoundCount"] == 2
    assert (result["initialWeightGrams"], result["finalWeightGrams"]) == (500, 1400)


def test_fullness_source_attachment_is_boot_only_and_never_changes_outputs(runtime):
    lib, endpoint, preparation, *_ = runtime
    lib.TestUltrasonic_Init()
    writes = lib.TestFacts_Writes()
    assert lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    assert not lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    assert not lib.TestUltrasonic_Trigger()
    assert exchange(runtime, "BOOT_PROBE", {"probeId": 1})[0][1]["mcuBootId"] == 0
    assert exchange(runtime, "BIND_BOOT", {"probeId": 1, "proposedMcuBootId": 42})[0][1]["status"] == "BOUND"
    assert not lib.McuWorkPreparation_AttachFullness(preparation, endpoint)
    assert lib.TestFacts_Writes() == writes and not lib.TestUltrasonic_Trigger()
