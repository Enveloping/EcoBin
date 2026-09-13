#ifndef ECOBIN_MCU_ENVIRONMENT_MONITOR_H
#define ECOBIN_MCU_ENVIRONMENT_MONITOR_H
#include "mcu_device_facts.h"
#include "mcu_configuration.h"
#include "ultrasonic_reader.h"

/* Native candidate foreground producer. Initialize the real smoke monitor and
 * actuator clock once at MCU boot. Call from the foreground loop, never from
 * Capture/IRQ or another owner. No GPIO outputs or control/admission effects.
 * 1 means a completed real ADC attempt was published, not that smoke is safe.
 */
uint8_t McuEnvironmentMonitor_PollSmoke(McuDeviceFacts *facts);
/* Copy a completed actual ultrasonic attempt for the board's sole port 1.
 * Does not start/retry acquisition; publish then retire exact observation.
 * Not a multi-sample fullness decision, configuration apply, or admission. */
uint8_t McuEnvironmentMonitor_PollUltrasonic(McuDeviceFacts *facts);
/* Explicit single acquisition using the verified active config, only after its
 * full-device applied facts match. No sampling from staged/default/other-port
 * settings, no claim that constructing a config means it has been applied. */
uint32_t McuEnvironmentMonitor_StartUltrasonic(const McuDeviceFacts *facts, const McuConfiguration *configuration);
/* Shared foreground checks/publication for raw and reserved group acquisition.
 * Read verifies full applied identity, then returns the original active policy.
 * Publish uses the real owned observation; it never retires it or changes bags. */
uint8_t McuEnvironmentMonitor_ReadUltrasonicPolicy(const McuDeviceFacts *facts,
    const McuConfiguration *configuration, McuConfigFullnessPolicy *output);
uint8_t McuEnvironmentMonitor_PublishUltrasonic(McuDeviceFacts *facts, const UltrasonicObservation *observation);
#endif
