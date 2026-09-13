#include <assert.h>
#include "actuator_runtime.h"
#include "runtime_clock.h"

static uint32_t irq_mask;
static uint8_t pins, pinch;
static uint32_t enter(void) { uint32_t old = irq_mask; irq_mask = 1U; return old; }
static void leave(uint32_t old) { irq_mask = old; }
static uint8_t read_pinch(void) { return pinch; }
static void write_pins(uint8_t value) { pins = value; }
static const ActuatorHardware hardware = {enter, leave, read_pinch, write_pins};

int main(void)
{
    ActuatorSnapshot state;
    RuntimeClock_Init();
    ActuatorRuntime_Init(&hardware);
    assert(!ActuatorRuntime_Snapshot().door.target_valid);
    assert(ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_CLOSE));
    assert(pins == ACTUATOR_PB7);
    pinch = 1U;
    state = ActuatorRuntime_Snapshot();
    assert(state.door.pb7); /* A read cannot claim a GPIO write not yet done. */
    assert(!state.pinch_input_active); /* Same control update as the applied output. */
    assert(pins == ACTUATOR_PB7);
    ActuatorRuntime_Tick();
    state = ActuatorRuntime_Snapshot();
    assert(state.pinch_input_active);
    assert(state.control_uptime_ms == state.captured_uptime_ms);
    RuntimeClock_Advance(5U);
    state = ActuatorRuntime_Snapshot();
    assert(state.control_uptime_ms == 0U && state.captured_uptime_ms == 5U);
    assert(state.door.pinch_paused && state.door.target == MCU_DIRECTION_CLOSE);
    assert(pins == 0U);
    pinch = 0U;
    ActuatorRuntime_Tick();
    assert(pins == ACTUATOR_PB7);
    pinch = 1U;
    ActuatorRuntime_Tick();
    assert(ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_OPEN));
    pinch = 0U;
    ActuatorRuntime_Tick();
    assert(pins == ACTUATOR_PB6); /* Old CLOSE cannot reappear. */
    assert(!ActuatorRuntime_SetDoorTarget(99U));
    assert(pins == ACTUATOR_PB6);
    assert(ActuatorRuntime_Unlock(4800U));
    assert(!ActuatorRuntime_Unlock(0U));
    assert(pins == (ACTUATOR_PB6 | ACTUATOR_PB8));
    RuntimeClock_Advance(4790U); /* No main-loop work or weight sampling. */
    ActuatorRuntime_Tick();
    assert(pins == (ACTUATOR_PB6 | ACTUATOR_PB8));
    RuntimeClock_Advance(10U);
    ActuatorRuntime_Tick();
    assert(pins == ACTUATOR_PB6);
    assert(ActuatorRuntime_Unlock(4800U));
    assert(pins == (ACTUATOR_PB6 | ACTUATOR_PB8));
    irq_mask = 1U; /* Preserve the caller's already-disabled interrupt state. */
    ActuatorRuntime_StopForUpdate();
    assert(irq_mask == 1U);
    assert(pins == 0U); /* No extra timer tick is required for the F2 reply. */
    irq_mask = 0U;
    ActuatorRuntime_Tick();
    assert(!ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_CLOSE));
    assert(!ActuatorRuntime_Unlock(4800U));
    assert(pins == 0U);
    return 0;
}
