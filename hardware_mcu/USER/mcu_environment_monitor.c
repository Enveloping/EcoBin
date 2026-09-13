#include "mcu_environment_monitor.h"
#include "smoke_monitor.h"
#include "actuator_runtime.h"

uint8_t McuEnvironmentMonitor_PollSmoke(McuDeviceFacts *facts) {
    uint8_t state;
    ActuatorSnapshot completed;
    if (facts == NULL || !SmokeMonitor_UpdateSample()) return 0u;
    /* Capture immediately after this actual attempt, never at a later query.
     * Stable state is debounced by the existing monitor, not raw ADC voltage. */
    completed = ActuatorRuntime_Snapshot();
    switch (SmokeMonitor_GetState()) {
    case SMOKE_NORMAL: state = ECOBIN_UART_SMOKE_OBSERVATION_STATE_NORMAL; break;
    case SMOKE_ALARM: state = ECOBIN_UART_SMOKE_OBSERVATION_STATE_ALARM; break;
    default: state = ECOBIN_UART_SMOKE_OBSERVATION_STATE_UNAVAILABLE; break;
    }
    return McuDeviceFacts_PublishSmoke(facts, state, completed.captured_uptime_ms);
}
