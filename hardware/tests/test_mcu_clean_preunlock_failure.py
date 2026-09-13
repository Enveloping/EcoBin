"""Initial clean weighing failures under the rc.23 autonomous START flow."""
import pytest

from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_mcu_simplified_execution import final, library, runtime, setup, state, tick
from hardware.tests.test_mcu_work_preparation import take_samples


def finish_initial_timeout(runtime, samples):
    _, _, start, now = setup(runtime, clean=True, initial=False)
    if samples:
        now = take_samples(runtime, samples, start=now)
    now = tick(runtime, now, 5000 - now)
    now = tick(runtime, now, 0)
    return start, now, final(runtime, start, now, clean=True)


def test_no_initial_clean_weight_fails_without_unlock_or_fake_human_confirmation(runtime):
    start, now, result = finish_initial_timeout(runtime, [])
    observed = facts(runtime, now)
    assert result["finishReason"] == "FAILED"
    assert result["initialKind"] == "UNAVAILABLE"
    assert result["initialFaultCode"] == "WEIGHT_TIMEOUT"
    assert result["initialSampleCount"] == 0
    assert result["finalKind"] == "NOT_TAKEN"
    assert result["cleanActionSequence"] == 0
    assert not result["physicalCloseConfirmed"]
    assert not observed["cleanLockPowered"]
    assert state(runtime, start, now, clean=True)["status"] == "RESULT_HELD"


@pytest.mark.parametrize("count", [1, 4])
def test_insufficient_initial_clean_samples_preserve_count_without_unlock(runtime, count):
    _, now, result = finish_initial_timeout(runtime, [300] * count)
    assert result["finishReason"] == "FAILED"
    assert result["initialKind"] == "UNAVAILABLE"
    assert result["initialSampleCount"] == count
    assert result["initialWeightGrams"] == 0
    assert result["cleanActionSequence"] == 0
    assert not result["physicalCloseConfirmed"]
    assert not facts(runtime, now)["cleanLockPowered"]


@pytest.mark.parametrize(
    "samples,expected_kind,expected_weight",
    [([500] * 5, "STABLE_MEAN", 500), ([0, 1000] * 10, "TIMEOUT_MEDIAN", 500)],
)
def test_available_initial_clean_weight_starts_local_unlock_instead_of_false_failure(
        runtime, samples, expected_kind, expected_weight):
    _, _, start, now = setup(runtime, clean=True, initial=False)
    now = take_samples(runtime, samples, start=now)
    if expected_kind == "TIMEOUT_MEDIAN":
        now = tick(runtime, now, 5000 - now)
    now = tick(runtime, now, 0)
    observed = facts(runtime, now)
    assert state(runtime, start, now, clean=True)["phase"] == "CLEAN_UNLOCK_PULSE"
    assert observed["cleanLockPowered"]
    assert observed["measurementWeightGrams"] == expected_weight
    assert observed["measurementSampleCount"] == len(samples)
    assert observed["measurementElapsedMs"] == (1020 if expected_kind == "STABLE_MEAN" else 5000)


def test_initial_clean_failure_is_immutable_after_time_advances(runtime):
    start, now, result = finish_initial_timeout(runtime, [300] * 4)
    now = tick(runtime, now, 60000)
    assert final(runtime, start, now, clean=True) == result
    assert not facts(runtime, now)["cleanLockPowered"]
