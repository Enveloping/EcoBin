#include "mcu_delivery_execution.h"
#include "mcu_clean_execution.h"
#include "actuator_runtime.h"
#include "runtime_clock.h"
#include <string.h>
const uint8_t *TestSimple_DeliveryMeasurement(const McuDeliveryExecution *owner) { return owner->postclose.uid; }
uint16_t TestSimple_CleanSequence(const McuCleanExecution *owner) { return owner->action_sequence; }
uint8_t TestSimple_Apply(const McuConfiguration *configuration, void *context) {
    (void)context;
    return configuration->active.complete;
}
uint8_t TestSimple_EnableApply(McuWorkPreparation *owner, McuControlEndpoint *endpoint) {
    return McuWorkPreparation_SetConfigurationApply(owner, endpoint, TestSimple_Apply, 0);
}
/* A real production timer tick at a chosen critical-section boundary. The
 * original shared host AdvanceOnEntry only advances time, not driver state. */
static uint32_t preempt_after, preempt_delta;
static uint32_t enter(void) {
    if (preempt_after && --preempt_after == 0u) {
        RuntimeClock_Advance(preempt_delta);
        ActuatorRuntime_Tick();
    }
    return 0u;
}
static void leave(uint32_t previous) { (void)previous; }
static uint8_t input(void) { return 0u; }
static void output(uint8_t pins) { (void)pins; }
static const ActuatorHardware preempting_hardware = {enter, leave, input, output};
void TestSimple_InstallPreemptingHardware(void) {
    preempt_after = preempt_delta = 0u;
    ActuatorRuntime_Init(&preempting_hardware);
}
void TestSimple_Preempt(uint32_t after, uint32_t delta) { preempt_after = after; preempt_delta = delta; }
uint64_t TestSimple_Now(void) { return RuntimeClock_Now64Locked(); }
