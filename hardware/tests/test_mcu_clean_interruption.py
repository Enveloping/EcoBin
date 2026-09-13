"""Clean-operation expiry and control interruption in the autonomous MCU flow."""
import pytest

from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_mcu_simplified_execution import (
    final,
    library,
    request,
    runtime,
    setup,
    state,
    tick,
)
from hardware.tests.test_mcu_work_preparation import take_samples
from hardware.tests.test_native_configuration import inputs


def active_clean(runtime):
    _, cleanup, start, now = setup(runtime, clean=True)
    now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
    assert state(runtime, start, now, clean=True)["phase"] == "CLEAN_ACTIVE"
    return cleanup, start, now


def test_operation_expiry_cancels_without_fabricating_a_finish_button_or_closed_door(runtime):
    _, _, start, now = setup(runtime, clean=True)
    now = tick(runtime, now, start["operationWindowMs"])
    result = final(runtime, start, now, clean=True)
    assert result["finishReason"] == "CANCELLED"
    assert not result["physicalCloseConfirmed"]
    assert result["finalKind"] == "NOT_TAKEN"
    assert not facts(runtime, now)["cleanLockPowered"]


@pytest.mark.parametrize("stage", ["pulse", "active", "measuring"])
def test_update_cancels_each_clean_stage_and_preserves_only_observed_facts(runtime, stage):
    _, cleanup, start, now = setup(runtime, clean=True)
    if stage != "pulse":
        now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
    if stage == "measuring":
        assert request(runtime, cleanup, start, now, "CLEAN_FINISH_REQUESTED")
        now = take_samples(runtime, [100, 110], start=now, measurement=2)

    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, 0)
    result = final(runtime, start, now, clean=True)
    observed = facts(runtime, now)
    assert result["finishReason"] == "CANCELLED"
    assert result["physicalCloseConfirmed"] is (stage == "measuring")
    assert result["finalKind"] == ("INTERRUPTED" if stage == "measuring" else "NOT_TAKEN")
    assert result["finalSampleCount"] == (2 if stage == "measuring" else 0)
    assert observed["updateLatched"] and not observed["cleanLockPowered"]


def test_pinch_alone_is_not_a_clean_interruption_and_does_not_claim_the_door_closed(runtime):
    cleanup, start, now = active_clean(runtime)
    runtime[0].TestFacts_Pinch(1)
    now = tick(runtime, now, 1000)
    assert state(runtime, start, now, clean=True)["phase"] == "CLEAN_ACTIVE"
    assert not facts(runtime, now)["cleanLockPowered"]

    assert request(runtime, cleanup, start, now, "CLEAN_FINISH_REQUESTED")
    now = take_samples(runtime, [100] * 5, start=now, measurement=2)
    result = final(runtime, start, now, clean=True)
    assert result["finishReason"] == "CLEAN_CONFIRMED"
    assert result["physicalCloseConfirmed"]


def test_interrupted_clean_result_is_immutable_and_never_reenergizes_the_lock(runtime):
    _, start, now = active_clean(runtime)
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, 0)
    result = final(runtime, start, now, clean=True)
    now = tick(runtime, now, 60000)
    assert final(runtime, start, now, clean=True) == result
    assert not facts(runtime, now)["cleanLockPowered"]
