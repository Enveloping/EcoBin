"""Autonomous delivery cancellation keeps actual motion and weight facts."""
import pytest
import uart2_protocol as uart

from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_mcu_simplified_execution import (
    closed_measurement,
    final,
    library,
    runtime,
    select,
    setup,
    tick,
)
from hardware.tests.test_mcu_work_preparation import exchange, take_samples


def save_action(runtime, store, message, values, now):
    """Compatibility helper for the remaining historical action-journal tests."""
    raw = uart.encode_payload(message, values)
    receipt = store.save_native_actuator_event(message, raw)
    assert exchange(runtime, "ACTUATOR_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
    return raw, receipt


@pytest.mark.parametrize("opened", [False, True])
def test_update_cancels_first_autonomous_round_without_inventing_a_final_weight(runtime, opened):
    _, _, start, now = setup(runtime)
    if opened:
        now = tick(runtime, now, 100)
        assert facts(runtime, now)["pb6Output"]
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, start["deliveryAutoCloseMs"] + 200)

    observed = facts(runtime, now)
    assert observed["updateLatched"]
    assert not observed["pb6Output"] and not observed["pb7Output"]
    result = final(runtime, start, now)
    assert result["finishReason"] == "CANCELLED"
    assert result["finalKind"] == "NOT_TAKEN"
    assert result["finalWeightGrams"] == 0
    assert result["deliveryRoundCount"] == int(opened)


def test_update_in_the_close_reversing_interval_cancels_with_both_outputs_off(runtime):
    _, _, start, now = setup(runtime)
    now = tick(runtime, now, 100)
    now = tick(runtime, now, start["deliveryAutoCloseMs"])
    assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"]
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, 100)
    result = final(runtime, start, now)
    assert result["finishReason"] == "CANCELLED"
    assert result["deliveryRoundCount"] == 1
    assert result["finalKind"] == "NOT_TAKEN"
    assert not result["physicalCloseConfirmed"]


@pytest.mark.parametrize("second_opened", [False, True])
def test_second_round_cancellation_keeps_first_weight_and_actual_round_count(runtime, second_opened):
    execution, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [0] * 5, start=now, measurement=2)
    assert select(runtime, execution, now, "CONTINUE")
    if second_opened:
        now = tick(runtime, now, 100)
        assert facts(runtime, now)["pb6Output"]
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, start["deliveryAutoCloseMs"] + 200)

    result = final(runtime, start, now)
    assert result["finishReason"] == "CANCELLED"
    assert result["initialWeightGrams"] == 500
    assert result["finalKind"] == "NOT_TAKEN"
    assert result["deliveryRoundCount"] == 1 + int(second_opened)
    assert result["negativeWeightAnomaly"]
    assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"]


def test_cancelled_result_is_immutable_and_does_not_resume_motion(runtime):
    _, _, start, now = setup(runtime)
    now = tick(runtime, now, 100)
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, start["deliveryAutoCloseMs"] + 200)
    result = final(runtime, start, now)
    now = tick(runtime, now, 60000)
    assert final(runtime, start, now) == result
    assert not facts(runtime, now)["pb6Output"] and not facts(runtime, now)["pb7Output"]
