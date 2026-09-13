"""Initial delivery weighing failures under the rc.23 autonomous START flow."""
import pytest

from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_mcu_simplified_execution import final, library, runtime, setup, state, tick
from hardware.tests.test_mcu_work_preparation import take_samples


def finish_initial_timeout(runtime, samples):
    _, _, start, now = setup(runtime, initial=False)
    if samples:
        now = take_samples(runtime, samples, start=now)
    now = tick(runtime, now, 5000 - now)
    now = tick(runtime, now, 0)
    return start, now, final(runtime, start, now)


def test_no_initial_weight_fails_without_opening_or_inventing_a_zero_measurement(runtime):
    start, now, result = finish_initial_timeout(runtime, [])
    observed = facts(runtime, now)
    assert result["finishReason"] == "FAILED"
    assert result["initialKind"] == "UNAVAILABLE"
    assert result["initialFaultCode"] == "WEIGHT_TIMEOUT"
    assert result["initialSampleCount"] == 0
    assert result["finalKind"] == "NOT_TAKEN"
    assert result["deliveryRoundCount"] == 0
    assert not observed["pb6Output"]
    assert observed["lastDeliveryDoorCommand"] == "CLOSE"
    assert state(runtime, start, now)["status"] == "RESULT_HELD"


@pytest.mark.parametrize("samples", [[300], [300] * 4])
def test_insufficient_initial_samples_keep_the_actual_count_and_never_open(runtime, samples):
    _, now, result = finish_initial_timeout(runtime, samples)
    assert result["finishReason"] == "FAILED"
    assert result["initialKind"] == "UNAVAILABLE"
    assert result["initialSampleCount"] == len(samples)
    assert result["initialWeightGrams"] == 0
    assert result["finalKind"] == "NOT_TAKEN"
    assert result["deliveryRoundCount"] == 0
    assert not facts(runtime, now)["pb6Output"]


@pytest.mark.parametrize(
    "samples,expected_kind,expected_weight",
    [([500] * 5, "STABLE_MEAN", 500), ([0, 1000] * 10, "TIMEOUT_MEDIAN", 500)],
)
def test_available_initial_weight_enters_autonomous_open_instead_of_false_failure(
        runtime, samples, expected_kind, expected_weight):
    _, _, start, now = setup(runtime, initial=False)
    now = take_samples(runtime, samples, start=now)
    if expected_kind == "TIMEOUT_MEDIAN":
        now = tick(runtime, now, 5000 - now)
    now = tick(runtime, now, 0)
    assert state(runtime, start, now)["phase"] == "DELIVERY_OPEN_COMMAND"
    now = tick(runtime, now, 100)
    observed = facts(runtime, now)
    assert observed["pb6Output"]
    assert observed["measurementWeightGrams"] == expected_weight
    assert observed["measurementSampleCount"] == len(samples)
    assert observed["measurementElapsedMs"] == (1020 if expected_kind == "STABLE_MEAN" else 5000)


def test_initial_failure_is_immutable_after_time_advances(runtime):
    start, now, result = finish_initial_timeout(runtime, [300] * 4)
    now = tick(runtime, now, 60000)
    assert final(runtime, start, now) == result
    assert not facts(runtime, now)["pb6Output"]
