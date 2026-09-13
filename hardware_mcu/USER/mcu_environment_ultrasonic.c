#include "mcu_environment_monitor.h"
#include "ultrasonic_reader.h"
#include "actuator_runtime.h"
#include <string.h>

uint32_t McuEnvironmentMonitor_StartUltrasonic(const McuDeviceFacts *facts, const McuConfiguration *configuration) {
    McuConfigFullnessPolicy policy;
    return McuEnvironmentMonitor_ReadUltrasonicPolicy(facts, configuration, &policy)
        && (!facts->fullness_status || ActuatorRuntime_Snapshot().captured_uptime_ms > facts->fullness_captured_ms)
        ? UltrasonicReader_Begin(policy.echo_timeout_us) : 0u;
}

uint8_t McuEnvironmentMonitor_ReadUltrasonicPolicy(const McuDeviceFacts *facts,
    const McuConfiguration *configuration, McuConfigFullnessPolicy *output) {
    McuConfigFullnessPolicy policy;
    if (facts == NULL || configuration == NULL || output == NULL || facts->port_no != 1u || facts->config_staging
        || McuConfiguration_IsStaging(configuration)
        || !McuConfiguration_ReadFullnessPolicy(configuration, facts->port_no, &policy)
        || facts->config_version != policy.config_version || !policy.enabled
        || policy.sensor_kind != ECOBIN_UART_FULLNESS_SENSOR_KIND_ULTRASONIC
        || memcmp(facts->content_sha256, configuration->active.preimage + ECOBIN_UART_CONFIG_PREIMAGE_CONTENT_SHA256_OFFSET, 32u) != 0
        || memcmp(facts->mcu_sha256, configuration->active.expected_digest, 32u) != 0) return 0u;
    *output = policy;
    return 1u;
}

uint8_t McuEnvironmentMonitor_PollUltrasonic(McuDeviceFacts *facts) {
    UltrasonicObservation observation;
    if (facts == NULL || facts->port_no != 1u || !UltrasonicReader_Copy(&observation)) return 0u;
    return McuEnvironmentMonitor_PublishUltrasonic(facts, &observation)
        && UltrasonicReader_Retire(observation.attempt_sequence);
}

uint8_t McuEnvironmentMonitor_PublishUltrasonic(McuDeviceFacts *facts, const UltrasonicObservation *observation) {
    uint16_t distance;
    if (facts == NULL || facts->port_no != 1u || observation == NULL || observation->attempt_sequence == 0u
        || (observation->status != ULTRASONIC_VALID && observation->status != ULTRASONIC_UNAVAILABLE)
        || (observation->status == ULTRASONIC_VALID && (observation->pulse_us == 0u || observation->pulse_us > 100000u))
        || (observation->status == ULTRASONIC_UNAVAILABLE && observation->pulse_us != 0u)) return 0u;
    /* Preserve the existing HC-SR04 pulse_us/58 cm conversion, but retain mm
     * precision instead of first truncating to whole cm. No threshold decision. */
    distance = observation->status == ULTRASONIC_VALID ? (uint16_t)(observation->pulse_us * 10u / 58u) : 0u;
    return McuDeviceFacts_PublishFullness(facts, ECOBIN_UART_FULLNESS_OBSERVATION_KIND_ULTRASONIC,
        observation->status == ULTRASONIC_VALID ? ECOBIN_UART_FULLNESS_READ_STATUS_VALID : ECOBIN_UART_FULLNESS_READ_STATUS_UNAVAILABLE,
        observation->captured_ms, 0u, distance);
}
