#include <assert.h>
#include "actuator_runtime.h"
#include "runtime_clock.h"

static uint8_t pins;
static uint32_t enter(void) { return 0U; }
static void leave(uint32_t previous) { (void)previous; }
static uint8_t input(void) { return 0U; }
static void output(uint8_t value) { pins = value; assert((value & 3U) != 3U); }
static const ActuatorHardware hardware = {enter, leave, input, output};
static void advance(uint32_t ms) { RuntimeClock_Advance(ms); ActuatorRuntime_Tick(); }

int main(void)
{
    uint32_t token;
    ActuatorCleanPulse pulse;
    RuntimeClock_Init();
    ActuatorRuntime_Init(&hardware);
    assert(ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_CLOSE));
    advance(1020U);
    token = ActuatorRuntime_BeginCleanPulse(30000U, 4800U);
    assert(token && pins == (ACTUATOR_PB7 | ACTUATOR_PB8));
    pulse = ActuatorRuntime_CleanPulse();
    assert(pulse.present && pulse.token == token && pulse.powered_at_ms == 1020U);
    assert(pulse.off_deadline_ms == 5820U && !pulse.completed && !pulse.interrupted);
    assert(!ActuatorRuntime_ReleaseCleanPulse(token));
    assert(!ActuatorRuntime_BeginCleanPulse(30000U, 4800U));
    assert(!ActuatorRuntime_BeginDeliveryCycle(30000U, 1000U));
    assert(!ActuatorRuntime_Unlock(9999U));
    assert(!ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_OPEN));
    advance(4799U);
    assert(pins == (ACTUATOR_PB7 | ACTUATOR_PB8));
    advance(1U); /* No foreground code or acknowledgement is needed. */
    assert(pins == ACTUATOR_PB7);
    pulse = ActuatorRuntime_CleanPulse();
    assert(pulse.completed && !pulse.interrupted && pulse.off_at_ms == 5820U);
    advance(10000U);
    assert(ActuatorRuntime_CleanPulse().off_at_ms == 5820U);
    assert(!ActuatorRuntime_BeginCleanPulse(30000U, 4800U));
    assert(!ActuatorRuntime_ReleaseCleanPulse(token + 1U));
    assert(ActuatorRuntime_ReleaseCleanPulse(token));
    assert(pins == ACTUATOR_PB7);
    assert(!ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_OPEN));
    assert(!ActuatorRuntime_Unlock(4800U));
    assert(ActuatorRuntime_BeginCleanPulse(30000U, 4800U) == token + 1U);
    advance(200U);
    ActuatorRuntime_StopForUpdate();
    assert(pins == 0U);
    pulse = ActuatorRuntime_CleanPulse();
    assert(pulse.completed && pulse.interrupted && pulse.off_at_ms == 16020U);
    advance(5000U);
    assert(ActuatorRuntime_CleanPulse().off_at_ms == 16020U);
    assert(ActuatorRuntime_ReleaseCleanPulse(token + 1U));
    assert(!ActuatorRuntime_BeginCleanPulse(30000U, 4800U));
    RuntimeClock_Init();
    ActuatorRuntime_Init(&hardware);
    assert(!ActuatorRuntime_BeginCleanPulse(100U, 4800U)); /* No CLOSE context. */
    assert(ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_OPEN));
    assert(!ActuatorRuntime_BeginCleanPulse(100U, 4800U));
    assert(pins == ACTUATOR_PB6);
    assert(ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_CLOSE));
    assert(!ActuatorRuntime_BeginCleanPulse(0U, 4800U));
    assert(!ActuatorRuntime_BeginCleanPulse(100U, 0U));
    assert(!ActuatorRuntime_BeginCleanPulse(100U, 0x80000000UL));
    assert(pins == ACTUATOR_PB7 && !ActuatorRuntime_CleanPulse().present);
    RuntimeClock_Advance(UINT32_MAX - 100U);
    token = ActuatorRuntime_BeginCleanPulse((uint64_t)UINT32_MAX + 1000U, 4800U);
    assert(token);
    advance(5000U); /* Late timer reports its actual OFF time, not the deadline. */
    pulse = ActuatorRuntime_CleanPulse();
    assert(pulse.off_deadline_ms == (uint64_t)UINT32_MAX + 4700U);
    assert(pulse.off_at_ms == (uint64_t)UINT32_MAX + 4900U && pulse.completed);
    assert(pins == ACTUATOR_PB7 && ActuatorRuntime_ReleaseCleanPulse(token));
    RuntimeClock_Init();
    ActuatorRuntime_Init(&hardware);
    assert(ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_CLOSE));
    token = ActuatorRuntime_BeginCleanPulse(1000U, 4800U);
    assert(token);
    advance(100U);
    assert(!ActuatorRuntime_AbortCleanPulse(token + 1U));
    assert(pins == (ACTUATOR_PB7 | ACTUATOR_PB8));
    assert(ActuatorRuntime_AbortCleanPulse(token));
    pulse = ActuatorRuntime_CleanPulse();
    assert(pulse.present && pulse.completed && pulse.interrupted && pulse.off_at_ms == 100U);
    assert(pins == ACTUATOR_PB7 && !ActuatorRuntime_Snapshot().update_latched);
    advance(1000U);
    assert(ActuatorRuntime_AbortCleanPulse(token));
    assert(ActuatorRuntime_CleanPulse().off_at_ms == 100U);
    assert(ActuatorRuntime_ReleaseCleanPulse(token));
    assert(!ActuatorRuntime_AbortCleanPulse(token));
    token = ActuatorRuntime_BeginCleanPulse(2000U, 100U);
    advance(100U);
    assert(ActuatorRuntime_AbortCleanPulse(token));
    pulse = ActuatorRuntime_CleanPulse();
    assert(pulse.off_at_ms == 1200U && !pulse.interrupted);
    return 0;
}
