"""Autonomous continued delivery under one START and one final result.

The MCU owns each CONTINUE/END choice, door cycle, measurement and immutable
WORK_RESULT.  These tests deliberately have no QUERY_PROCESS_EVENT,
PROCESS_EVENT_SAVED or Pi-side per-action authorization dependency.
"""
import ctypes as c

import pytest
import uart2_protocol as uart

from edge_store import EdgeStore
from mcu_result_handoff import McuResultHandoff
from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_mcu_simplified_execution import (
    closed_measurement,
    final,
    library,
    runtime,
    select,
    setup,
    state,
    tick,
)
from hardware.tests.test_mcu_work_preparation import exchange, take_samples
from hardware.tests.test_native_configuration import inputs


def finish_round(runtime, delivery, start, now, grams, measurement, *, selection="END"):
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [grams] * 5, start=now, measurement=measurement)
    uid = c.string_at(runtime[0].TestSimple_DeliveryMeasurement(delivery), 16)
    assert select(runtime, delivery, now, selection, uid=uid)
    return now, uid


def test_continue_opens_and_closes_round_two_under_the_original_start(runtime):
    delivery, _, start, now = setup(runtime)
    now, first_uid = finish_round(
        runtime, delivery, start, now, 1000, 2, selection="CONTINUE"
    )

    now = tick(runtime, now, 100)
    opened = facts(runtime, now)
    assert opened["lastDeliveryDoorCommand"] == "OPEN"
    assert opened["pb6Output"] and not opened["pb7Output"]
    assert state(runtime, start, now)["phase"] == "DELIVERY_OPEN_COUNTDOWN"
    assert not select(runtime, delivery, now, "END", uid=first_uid)

    now = tick(runtime, now, start["deliveryAutoCloseMs"])
    reversing = facts(runtime, now)
    assert not reversing["pb6Output"] and not reversing["pb7Output"]
    now = tick(runtime, now, 100)
    closing = facts(runtime, now)
    assert closing["lastDeliveryDoorCommand"] == "CLOSE"
    assert closing["pb7Output"] and not closing["pb6Output"]
    now = tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    now = take_samples(runtime, [1500] * 5, start=now, measurement=3)
    assert select(runtime, delivery, now)
    result = final(runtime, start, now)
    assert result["originCommandUid"] == start["mcuCommandUid"]
    assert (result["deliveryRoundCount"], result["initialWeightGrams"],
            result["finalWeightGrams"]) == (2, 500, 1500)


@pytest.mark.parametrize("mode", ["median", "unavailable", "selection_timeout"])
def test_later_round_keeps_measurement_method_or_failure_and_finishes_original_work(runtime, mode):
    first = 0 if mode == "unavailable" else 1700
    delivery, _, start, now = setup(runtime)
    now, _ = finish_round(runtime, delivery, start, now, first, 2, selection="CONTINUE")
    now = closed_measurement(runtime, start, now)
    began = now
    if mode == "median":
        now = take_samples(runtime, [-500, 1500] * 10, start=now, measurement=3)
        now = tick(runtime, now, began + 5000 - now)
        assert select(runtime, delivery, now)
    elif mode == "selection_timeout":
        now = take_samples(runtime, [2000] * 5, start=now, measurement=3)
        now = tick(runtime, now, start["continueDeliveryWaitMs"])
    else:
        now = tick(runtime, now, 5000)

    result = final(runtime, start, now)
    expected = {
        "median": ("TIMEOUT_MEDIAN", "DELIVERY_END", True),
        "unavailable": ("UNAVAILABLE", "FAILED", True),
        "selection_timeout": ("STABLE_MEAN", "DELIVERY_WINDOW_EXPIRED", False),
    }[mode]
    assert result["deliveryRoundCount"] == 2
    assert result["initialWeightGrams"] == 500
    assert result["initialMeasurementUid"] != result["finalMeasurementUid"]
    assert (result["finalKind"], result["finishReason"],
            result["negativeWeightAnomaly"]) == expected


@pytest.mark.parametrize("reason", ["update", "guard", "configuration", "sequence_exhausted"])
def test_continue_uses_mcu_owned_start_facts_and_control_safety_boundaries(runtime, reason):
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [1000] * 5, start=now, measurement=2)
    lib, endpoint, _, _, prerequisites, *_ = runtime

    if reason == "update":
        lib.ActuatorRuntime_StopForUpdate()
    elif reason == "guard":
        prerequisites["error"] = 65535
    elif reason == "configuration":
        assert lib.McuDeviceFacts_PublishConfiguration(
            lib.TestPreparation_Facts(endpoint),
            start["configVersion"] + 1,
            bytes.fromhex("aa" * 32),
            bytes.fromhex("bb" * 32),
            0,
        )
    else:
        # The retired process/action journal is diagnostic only. Exhausting its
        # sequence space cannot become a new Pi permission gate.
        lib.TestPreparation_SetEventSequence(endpoint, 0xFFFFFFFE)

    selected = select(runtime, delivery, now, "CONTINUE")
    prerequisites["error"] = 0
    if reason in {"update", "configuration"}:
        # The button frame can be accepted before the control loop observes an
        # independently changed control prerequisite.  Acceptance is not
        # permission to move: the next poll must cancel and keep both outputs
        # de-energised.
        assert selected
        now = tick(runtime, now, 0)
        result = final(runtime, start, now)
        assert result["finishReason"] == (
            "CANCELLED" if reason == "update" else "FAILED"
        )
        assert result["deliveryRoundCount"] == 1
        stopped = facts(runtime, now)
        assert not stopped["pb6Output"]
        if reason == "update":
            assert not stopped["pb7Output"]
        else:
            # A changed configuration cannot start another opening cycle.  The
            # already-established CLOSE target remains energised.
            assert stopped["pb7Output"]
            assert stopped["lastDeliveryDoorCommand"] == "CLOSE"
        return

    assert selected
    now, _ = finish_round(runtime, delivery, start, now, 1300, 3)
    result = final(runtime, start, now)
    assert result["finishReason"] == "DELIVERY_END"
    assert result["deliveryRoundCount"] == 2
    assert result["originCommandUid"] == start["mcuCommandUid"]


def test_valid_last_moment_continue_starts_one_new_cycle_without_pi_ack(runtime):
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [1000] * 5, start=now, measurement=2)
    now = tick(runtime, now, start["continueDeliveryWaitMs"] - 1)
    assert select(runtime, delivery, now, "CONTINUE")

    now = tick(runtime, now, 100)
    assert facts(runtime, now)["lastDeliveryDoorCommand"] == "OPEN"
    assert state(runtime, start, now)["phase"] == "DELIVERY_OPEN_COUNTDOWN"
    now, _ = finish_round(runtime, delivery, start, now, 1400, 3)
    result = final(runtime, start, now)
    assert result["deliveryRoundCount"] == 2
    assert result["finishReason"] == "DELIVERY_END"


def test_diagnostic_actuator_journal_capacity_cannot_partially_start_continue(runtime):
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [1000] * 5, start=now, measurement=2)
    reserve = runtime[0].McuControlEndpoint_ReserveActuatorEvents
    reservation = reserve.argtypes[2]._type_()
    assert reserve(runtime[1], 8, c.byref(reservation))

    assert select(runtime, delivery, now, "CONTINUE")
    now, _ = finish_round(runtime, delivery, start, now, 1300, 3)
    result = final(runtime, start, now)
    assert result["deliveryRoundCount"] == 2
    assert result["finishReason"] == "DELIVERY_END"
    # No autonomous business step requires the old diagnostic mailbox.
    reply = exchange(runtime, "QUERY_ACTUATOR_EVENT", {
        "queryId": 100,
        "targetMcuBootId": 42,
        "afterMcuEventSequence": 0,
    }, now=now)
    assert reply[0][1]["status"] == "NOT_FOUND"


def test_continue_open_ignores_pb5_and_automatic_close_pauses_then_resumes(runtime):
    delivery, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [1000] * 5, start=now, measurement=2)
    runtime[0].TestFacts_Pinch(1)
    assert select(runtime, delivery, now, "CONTINUE")

    now = tick(runtime, now, 100)
    opened = facts(runtime, now)
    assert opened["lastDeliveryDoorCommand"] == "OPEN"
    assert opened["pb6Output"] and not opened["pinchPaused"]
    now = tick(runtime, now, start["deliveryAutoCloseMs"])
    now = tick(runtime, now, 100)
    paused = facts(runtime, now)
    assert paused["lastDeliveryDoorCommand"] == "CLOSE" and paused["pinchPaused"]
    assert not paused["pb6Output"] and not paused["pb7Output"]

    runtime[0].TestFacts_Pinch(0)
    runtime[0].ActuatorRuntime_Tick()
    resumed = facts(runtime, now)
    assert resumed["pb7Output"] and not resumed["pinchPaused"]
    now = tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    now = take_samples(runtime, [1300] * 5, start=now, measurement=3)
    assert select(runtime, delivery, now)
    assert final(runtime, start, now)["finishReason"] == "DELIVERY_END"


@pytest.mark.parametrize("weights,anomaly", [
    ([1700, 1201], False),
    ([1700, 1200], True),
    ([0, 1700], True),
    ([1700, 1000, 2500], True),
])
def test_rounds_keep_initial_weight_and_latch_each_round_drop_not_total_net(runtime, weights, anomaly):
    delivery, _, start, now = setup(runtime)
    previous_uid = None
    for index, grams in enumerate(weights, start=1):
        now = closed_measurement(runtime, start, now)
        now = take_samples(runtime, [grams] * 5, start=now, measurement=index + 1)
        current_uid = c.string_at(runtime[0].TestSimple_DeliveryMeasurement(delivery), 16)
        if previous_uid is not None:
            assert not select(runtime, delivery, now, "END", uid=previous_uid)
        selection = "END" if index == len(weights) else "CONTINUE"
        assert select(runtime, delivery, now, selection, uid=current_uid)
        previous_uid = current_uid

    result = final(runtime, start, now)
    assert result["deliveryRoundCount"] == len(weights)
    assert result["initialWeightGrams"] == 500
    assert result["finalWeightGrams"] == weights[-1]
    assert result["initialMeasurementUid"] != result["finalMeasurementUid"]
    assert result["negativeWeightAnomaly"] is anomaly
    assert result["originCommandUid"] == start["mcuCommandUid"]
    assert result["finishReason"] == "DELIVERY_END"
    now = tick(runtime, now, 60000)
    assert final(runtime, start, now) == result


@pytest.mark.parametrize("loss", ["before_delivery", "reply_lost"])
def test_pi_restart_or_result_reply_loss_does_not_repeat_cycle_or_duplicate_result(runtime, tmp_path, loss):
    delivery, _, start, now = setup(runtime)
    now, _ = finish_round(
        runtime, delivery, start, now, 1000, 2, selection="CONTINUE"
    )
    path = tmp_path / "edge.db"
    if loss == "before_delivery":
        # Restart the Pi after the MCU has accepted CONTINUE but before the
        # autonomous second cycle.  There is intentionally no Pi-side action
        # intent or acknowledgement to reconstruct.
        restarted = EdgeStore(str(path))
        restarted.initialize()
        restarted.close()

    now, _ = finish_round(runtime, delivery, start, now, 1500, 3)
    result = final(runtime, start, now)
    if loss == "reply_lost":
        # Reading the complete result does not consume it.  If that UART reply
        # is lost, the Pi can query the same immutable authority again; no door
        # cycle or per-action acknowledgement is replayed.
        assert final(runtime, start, now) == result
    raw = uart.encode_payload("WORK_RESULT", result)
    identity = uart.decode_payload("RESULT_SAVED", raw[:60])
    store = EdgeStore(str(path))
    store.initialize()
    try:
        if loss == "reply_lost":
            handoff = McuResultHandoff(store, lambda frame: len(frame), identity)
            assert handoff.accept_frame(uart.encode_frame("WORK_RESULT", 1, raw), now)
        store.close()
        store = EdgeStore(str(path))
        store.initialize()
        handoff = McuResultHandoff(store, lambda frame: len(frame), identity)
        assert handoff.accept_frame(uart.encode_frame("WORK_RESULT", 2, raw), now)
        assert store.get_native_mcu_result(42, result["resultSequence"])["payload"] == raw
        assert len(store.list_native_result_report_tasks()) == 1
        assert final(runtime, start, now) == result
        assert result["deliveryRoundCount"] == 2
        assert exchange(runtime, "RESULT_SAVED", payload=raw[:60], now=now)[0][1]["status"] == "RELEASED"
    finally:
        store.close()
