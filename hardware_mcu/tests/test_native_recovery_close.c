#include <assert.h>
#include "actuator_runtime.h"
#include "runtime_clock.h"

static uint8_t pins, pinch;
static uint32_t enter(void) { return 0U; }
static void leave(uint32_t old) { (void)old; }
static uint8_t input(void) { return pinch; }
static void output(uint8_t value) { pins = value; assert((value & 3U) != 3U); }
static const ActuatorHardware hardware = {enter, leave, input, output};
static void advance(uint32_t ms) { RuntimeClock_Advance(ms); ActuatorRuntime_Tick(); }

int main(void)
{
    uint32_t token, next;
    ActuatorRecoveryClose close;
    RuntimeClock_Init();
    ActuatorRuntime_Init(&hardware);
    assert(ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_OPEN));
    assert(pins == ACTUATOR_PB6);
    token = ActuatorRuntime_BeginRecoveryClose(1000U);
    assert(token && pins == 0U);
    assert(!ActuatorRuntime_ReleaseRecoveryClose(token));
    assert(!ActuatorRuntime_BeginDeliveryCycle(1000U, 1000U));
    assert(!ActuatorRuntime_BeginRecoveryClose(1000U));
    advance(99U);
    assert(pins == 0U);
    RuntimeClock_Advance(1U);
    assert(!ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_OPEN));
    assert(!ActuatorRuntime_Unlock(1000U));
    assert(pins == 0U); /* Rejected legacy callers do not dispatch the timer. */
    pinch = 1U;
    ActuatorRuntime_Tick();
    close = ActuatorRuntime_RecoveryClose();
    assert(close.completed && !close.rejected && close.terminal_at_ms == 100U);
    assert(pins == 0U && ActuatorRuntime_Snapshot().door.pinch_paused);
    pinch = 0U;
    ActuatorRuntime_Tick();
    assert(pins == ACTUATOR_PB7);
    assert(!ActuatorRuntime_ReleaseRecoveryClose(token + 1U));
    assert(ActuatorRuntime_ReleaseRecoveryClose(token));
    assert(pins == ACTUATOR_PB7);
    next = ActuatorRuntime_BeginRecoveryClose(101U);
    assert(next > token && pins == ACTUATOR_PB7);
    close = ActuatorRuntime_RecoveryClose();
    assert(close.completed && close.coalesced && close.terminal_at_ms == 100U);
    assert(!ActuatorRuntime_BeginCleanPulse(1000U, 100U));
    assert(ActuatorRuntime_ReleaseRecoveryClose(next));
    token = ActuatorRuntime_BeginCleanPulse(1000U, 100U);
    assert(token && pins == (ACTUATOR_PB7 | ACTUATOR_PB8));
    assert(!ActuatorRuntime_BeginRecoveryClose(1000U));
    assert(pins == (ACTUATOR_PB7 | ACTUATOR_PB8));
    advance(100U);
    assert(ActuatorRuntime_ReleaseCleanPulse(token));
    token = ActuatorRuntime_BeginDeliveryCycle(1000U, 1000U);
    assert(token && !ActuatorRuntime_BeginRecoveryClose(1000U));
    RuntimeClock_Init();
    ActuatorRuntime_Init(&hardware);
    advance(UINT32_MAX - 50U);
    token = ActuatorRuntime_BeginRecoveryClose((uint64_t)UINT32_MAX + 1000U);
    assert(token);
    advance(100U);
    assert(pins == ACTUATOR_PB7);
    assert(ActuatorRuntime_RecoveryClose().terminal_at_ms == (uint64_t)UINT32_MAX + 50U);
    RuntimeClock_Init();
    ActuatorRuntime_Init(&hardware);
    assert(!ActuatorRuntime_RecoveryClose().present && pins == 0U);
    advance(1000U);
    assert(pins == 0U); /* Reset does not resume an old action. */
    return 0;
}
