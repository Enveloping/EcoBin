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
    uint32_t token;
    ActuatorDeliveryCycle cycle;
    RuntimeClock_Init();
    ActuatorRuntime_Init(&hardware);
    assert(ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_CLOSE));
    token = ActuatorRuntime_BeginDeliveryCycle(1000U, 1000U);
    assert(token && pins == 0U);
    advance(99U);
    assert(pins == 0U);
    RuntimeClock_Advance(1U);
    assert(!ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_CLOSE));
    assert(pins == 0U); /* A rejected legacy entry must not dispatch the timer. */
    ActuatorRuntime_Tick();
    assert(pins == ACTUATOR_PB6);
    cycle = ActuatorRuntime_DeliveryCycle();
    assert(cycle.token == token && cycle.opened && cycle.opened_at_ms == 100U);
    assert(!ActuatorRuntime_BeginDeliveryCycle(2000U, 2000U));
    advance(999U);
    assert(pins == ACTUATOR_PB6);
    advance(1U);
    assert(pins == 0U);
    advance(99U);
    assert(pins == 0U);
    advance(1U);
    assert(pins == ACTUATOR_PB7);
    cycle = ActuatorRuntime_DeliveryCycle();
    assert(cycle.closed && cycle.closed_at_ms == 1200U);
    assert(cycle.opened_at_ms == 100U); /* Foreground absence loses neither edge. */
    assert(ActuatorRuntime_ReleaseDeliveryCycle(token));
    assert(pins == ACTUATOR_PB7); /* Releasing bookkeeping does not stop CLOSE. */
    assert(!ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_OPEN));
    assert(!ActuatorRuntime_Unlock(1000U)); /* No legacy escape after native mode. */
    assert(pins == ACTUATOR_PB7);
    RuntimeClock_Init();
    ActuatorRuntime_Init(&hardware);
    RuntimeClock_Advance(UINT32_MAX - 50U);
    token = ActuatorRuntime_BeginDeliveryCycle((uint64_t)UINT32_MAX + 500U, 1000U);
    assert(token && pins == 0U);
    advance(99U);
    assert(pins == 0U);
    advance(1U);
    assert(pins == ACTUATOR_PB6);
    assert(ActuatorRuntime_DeliveryCycle().opened_at_ms == (uint64_t)UINT32_MAX + 50U);
    assert(!ActuatorRuntime_ReleaseDeliveryCycle(token));
    advance(1000U);
    assert(pins == 0U);
    advance(100U);
    assert(pins == ACTUATOR_PB7);
    assert(!ActuatorRuntime_ReleaseDeliveryCycle(token + 1U));
    assert(ActuatorRuntime_ReleaseDeliveryCycle(token));
    return 0;
}
