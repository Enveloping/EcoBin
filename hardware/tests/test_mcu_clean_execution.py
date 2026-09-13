"""START clean operation -> MCU-owned local unlock/finish state machine."""
import ctypes as c

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


def enable(runtime):
    """Compatibility helper for older component tests; normal flow uses setup()."""
    lib, endpoint, preparation, *_ = runtime
    owner = (c.c_uint64 * 64)()
    assert lib.McuCleanExecution_Attach(owner, preparation, endpoint)
    return owner


def advance(runtime, now, duration, *, poll=True):
    """Advance the actual C runtime while retaining the historical helper API."""
    lib, endpoint, preparation, *_ = runtime
    lib.RuntimeClock_Advance(duration)
    lib.ActuatorRuntime_Tick()
    now += duration
    if poll:
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, now)
    return now


def test_start_autonomously_pulses_clean_lock_once_then_waits_for_local_input(runtime):
    _, cleanup, start, now = setup(runtime, clean=True)
    pulse_ms = inputs()["device"]["cleanSolenoidPulseMs"]

    powered = facts(runtime, now)
    assert powered["cleanLockPowered"]
    assert state(runtime, start, now, clean=True)["phase"] == "CLEAN_UNLOCK_PULSE"

    now = tick(runtime, now, pulse_ms)
    released = facts(runtime, now)
    assert not released["cleanLockPowered"]
    assert state(runtime, start, now, clean=True)["phase"] == "CLEAN_ACTIVE"
    assert cleanup


def test_local_reopen_and_finish_buttons_produce_one_complete_clean_result(runtime):
    _, cleanup, start, now = setup(runtime, clean=True)
    pulse_ms = inputs()["device"]["cleanSolenoidPulseMs"]
    now = tick(runtime, now, pulse_ms)

    assert request(runtime, cleanup, start, now, "CLEAN_UNLOCK_REQUESTED")
    assert not request(runtime, cleanup, start, now, "CLEAN_UNLOCK_REQUESTED", sequence=0)
    assert facts(runtime, now)["cleanLockPowered"]
    now = tick(runtime, now, pulse_ms)
    assert not facts(runtime, now)["cleanLockPowered"]

    assert request(runtime, cleanup, start, now, "CLEAN_FINISH_REQUESTED")
    assert not request(runtime, cleanup, start, now, "CLEAN_FINISH_REQUESTED")
    now = take_samples(runtime, [100] * 5, start=now, measurement=2)
    result = final(runtime, start, now, clean=True)
    assert result["finishReason"] == "CLEAN_CONFIRMED"
    assert result["physicalCloseConfirmed"]
    assert result["cleanActionSequence"] == 2
    assert result["initialWeightGrams"] == 500
    assert result["finalWeightGrams"] == 100


def test_pinch_input_does_not_block_clean_lock_pulse(runtime):
    lib = runtime[0]
    lib.TestFacts_Pinch(1)
    _, cleanup, start, now = setup(runtime, clean=True)
    assert facts(runtime, now)["cleanLockPowered"]

    now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
    assert not facts(runtime, now)["cleanLockPowered"]
    assert state(runtime, start, now, clean=True)["phase"] == "CLEAN_ACTIVE"

    assert request(runtime, cleanup, start, now, "CLEAN_FINISH_REQUESTED")
    now = take_samples(runtime, [100] * 5, start=now, measurement=2)
    assert final(runtime, start, now, clean=True)["finishReason"] == "CLEAN_CONFIRMED"


def test_update_during_active_clean_deenergizes_lock_and_cancels_without_fake_close(runtime):
    _, _, start, now = setup(runtime, clean=True)
    now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, 0)

    observed = facts(runtime, now)
    assert observed["updateLatched"] and not observed["cleanLockPowered"]
    result = final(runtime, start, now, clean=True)
    assert result["finishReason"] == "CANCELLED"
    assert not result["physicalCloseConfirmed"]
    assert result["finalKind"] == "NOT_TAKEN"
