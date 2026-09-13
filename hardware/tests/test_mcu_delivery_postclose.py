"""Fresh post-close weighing under the rc.23 autonomous delivery flow."""
import ctypes as c
import uuid

import pytest

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
from hardware.tests.test_mcu_work_preparation import original_scope, take_samples
from hardware.tests.test_native_configuration import inputs


def closed_cycle(runtime, tmp_path, **start_options):
    """Compatibility wrapper returning the old tuple around a current START cycle."""
    if start_options:
        raise ValueError("legacy START overrides are not supported by the autonomous fixture")
    execution, _, start, now = setup(runtime)
    measurement_uid = str(uuid.UUID(bytes=c.string_at(
        runtime[0].TestSimple_DeliveryMeasurement(execution), 16)))
    initial = {"measurementUid": measurement_uid, "reportedWeightGrams": 500, "uptimeMs": now}
    now = tick(runtime, now, 100)
    now = tick(runtime, now, start["deliveryAutoCloseMs"])
    now = tick(runtime, now, 100)
    scope = original_scope(start) | {
        "eventMessageType": "WORK_POSTCLOSE_WEIGHT_READY",
        "stepSequence": 1,
        "configVersion": start["configVersion"],
    }
    return execution, start, initial, scope, now


def advance(runtime, now, delta, *, poll=True):
    """Retain the historical helper name while advancing the actual C runtime."""
    if poll:
        return tick(runtime, now, delta)
    runtime[0].RuntimeClock_Advance(delta)
    runtime[0].ActuatorRuntime_Tick()
    return now + delta


def begin_postclose_measurement(runtime):
    execution, _, start, now = setup(runtime)
    now = closed_measurement(runtime, start, now)
    assert state(runtime, start, now)["phase"] == "DELIVERY_POSTCLOSE_MEASURING"
    return execution, start, now


def test_close_travel_wait_starts_a_fresh_second_measurement_at_its_real_deadline(runtime):
    execution, _, start, now = setup(runtime)
    now = tick(runtime, now, 100)
    now = tick(runtime, now, start["deliveryAutoCloseMs"])
    now = tick(runtime, now, 100)
    wait_ms = inputs()["device"]["deliveryDoorTravelWaitMs"]
    now = tick(runtime, now, wait_ms - 1)
    assert state(runtime, start, now)["phase"] == "DELIVERY_CLOSE_TRAVEL_WAIT"
    assert facts(runtime, now)["measurementSequence"] == 1
    now = tick(runtime, now, 1)
    observed = facts(runtime, now)
    assert state(runtime, start, now)["phase"] == "DELIVERY_POSTCLOSE_MEASURING"
    assert observed["measurementSequence"] == 2
    assert observed["measurementState"] == "RUNNING"
    assert execution


def test_postclose_uses_fresh_stable_samples_and_builds_the_final_result(runtime):
    execution, start, now = begin_postclose_measurement(runtime)
    now = take_samples(runtime, [1000, 1050, 950, 1010, 990], start=now, measurement=2)
    observed = facts(runtime, now)
    assert observed["measurementSequence"] == 2
    assert observed["measurementWeightGrams"] == 1000
    assert observed["measurementSampleCount"] == 5
    assert observed["measurementSpanGrams"] == 100
    assert observed["measurementElapsedMs"] == 1020
    assert state(runtime, start, now)["phase"] == "DELIVERY_WAIT_SELECTION"
    assert select(runtime, execution, now)
    result = final(runtime, start, now)
    assert result["initialWeightGrams"] == 500
    assert result["finalWeightGrams"] == 1000
    assert result["finalKind"] == "STABLE_MEAN"


@pytest.mark.parametrize("samples", [[], [1000] * 4])
def test_postclose_timeout_is_explicitly_unavailable_and_never_reuses_initial_weight(runtime, samples):
    _, start, now = begin_postclose_measurement(runtime)
    began = now
    if samples:
        now = take_samples(runtime, samples, start=now, measurement=2)
    now = tick(runtime, now, began + 5000 - now)
    result = final(runtime, start, now)
    assert result["finishReason"] == "FAILED"
    assert result["initialWeightGrams"] == 500
    assert result["finalKind"] == "UNAVAILABLE"
    assert result["finalWeightGrams"] == 0
    assert result["finalSampleCount"] == len(samples)
    assert result["finalElapsedMs"] == 5000
    assert result["finalFaultCode"] == "WEIGHT_TIMEOUT"


def test_unstable_postclose_samples_use_the_five_second_median(runtime):
    execution, start, now = begin_postclose_measurement(runtime)
    began = now
    samples = [-500, 1500] * 10
    now = take_samples(runtime, samples, start=now, measurement=2)
    now = tick(runtime, now, began + 5000 - now)
    assert select(runtime, execution, now)
    result = final(runtime, start, now)
    assert result["finishReason"] == "DELIVERY_END"
    assert result["finalKind"] == "TIMEOUT_MEDIAN"
    assert result["finalWeightGrams"] == 500
    assert result["finalSampleCount"] == 20
    assert result["finalSpanGrams"] == 2000
    assert result["finalElapsedMs"] == 5000


def test_pinch_during_close_does_not_block_postclose_weight_after_release(runtime):
    execution, _, start, now = setup(runtime)
    runtime[0].TestFacts_Pinch(1)
    now = tick(runtime, now, 100)
    now = tick(runtime, now, start["deliveryAutoCloseMs"])
    now = tick(runtime, now, 100)
    assert facts(runtime, now)["pinchPaused"]
    runtime[0].TestFacts_Pinch(0)
    runtime[0].ActuatorRuntime_Tick()
    now = tick(runtime, now, inputs()["device"]["deliveryDoorTravelWaitMs"])
    now = take_samples(runtime, [900] * 5, start=now, measurement=2)
    assert select(runtime, execution, now)
    assert final(runtime, start, now)["finalWeightGrams"] == 900
