/* Test-only hardware boundary. All facts/protocol/state logic is production C. */
#include "actuator_runtime.h"
#include "runtime_clock.h"
#include "mcu_device_facts.h"
static uint32_t mask, writes, advance_after, advance_ms, stop_after;
static uint8_t pinch;
static uint32_t enter(void) {
    uint32_t old = mask;
    mask = 1;
    if (advance_after && --advance_after == 0u) RuntimeClock_Advance(advance_ms);
    if (stop_after && --stop_after == 0u) ActuatorRuntime_StopForUpdate();
    return old;
}
static void leave(uint32_t old) { mask = old; }
static uint8_t input(void) { return pinch; }
static void output(uint8_t pins) { (void)pins; writes++; }
static const ActuatorHardware hardware = {enter, leave, input, output};
__declspec(dllexport) void TestFacts_InitHardware(void) {
    mask = writes = advance_after = advance_ms = stop_after = 0; pinch = 0; RuntimeClock_Init(); ActuatorRuntime_Init(&hardware);
}
/* One simulated timer preemption at a hardware critical-section boundary. */
__declspec(dllexport) void TestFacts_AdvanceOnEntry(uint32_t after, uint32_t ms) { advance_after = after; advance_ms = ms; }
__declspec(dllexport) void TestFacts_StopOnEntry(uint32_t after) { stop_after = after; }
__declspec(dllexport) void TestFacts_Pinch(uint8_t active) { pinch = active; }
/* Synthetic component-state loss, not an MCU reboot: keep clock and business
 * endpoint intact so the production owner must detect missing close context. */
__declspec(dllexport) void TestFacts_ReinitializeActuator(void) { ActuatorRuntime_Init(&hardware); }
__declspec(dllexport) uint32_t TestFacts_Writes(void) { return writes; }
