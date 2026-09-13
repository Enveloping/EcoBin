#include "mcu_opening_gate.h"
#include <string.h>
#define START(field) ECOBIN_UART_START_DELIVERY_SESSION_##field##_OFFSET
#define CLEAN(field) ECOBIN_UART_START_CLEAN_OPERATION_##field##_OFFSET
#define DEVICE(field) (ECOBIN_UART_CONFIG_PREIMAGE_DEVICE_OFFSET + ECOBIN_UART_CONFIG_DEVICE_BLOCK_##field##_OFFSET - ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET)

uint8_t McuOpeningGate_ConfigurationMatches(const McuWorkPreparation *owner, const McuControlEndpoint *endpoint) {
    McuConfigWeightPolicy policy;
    const uint8_t *start = owner->start_payload;
    const uint8_t *preimage = owner->configuration.active.preimage;
    return (uint8_t)(!McuConfiguration_IsStaging(&owner->configuration)
        && owner->configuration.active.boot_id == endpoint->session.boot_id
        && McuConfiguration_ReadWeightPolicy(&owner->configuration, start[START(PORT_NO)], &policy)
        && policy.enabled && !endpoint->facts.config_staging
        && policy.config_version == ecobin_uart_read_u64_be(start + START(CONFIG_VERSION))
        && policy.config_version == endpoint->facts.config_version
        && policy.config_version == owner->initial_meta.config_version
        && policy.calibration_version == owner->initial.calibration_version
        && memcmp(start + START(CONFIG_CONTENT_SHA256), preimage + ECOBIN_UART_CONFIG_PREIMAGE_CONTENT_SHA256_OFFSET, 32u) == 0
        && memcmp(endpoint->facts.content_sha256, preimage + ECOBIN_UART_CONFIG_PREIMAGE_CONTENT_SHA256_OFFSET, 32u) == 0
        && memcmp(endpoint->facts.mcu_sha256, owner->configuration.active.expected_digest, 32u) == 0);
}

uint16_t McuOpeningGate_StartLimits(const McuWorkPreparation *owner,
    const McuControlEndpoint *endpoint, uint64_t now, McuOpeningLimits *output) {
    McuOpeningLimits limits = {0};
    uint32_t duration;
    uint8_t clean;
    if (owner == NULL || endpoint == NULL || output == NULL || endpoint->application_context != owner
        || !owner->initial_ready || !owner->weight.retired || owner->weight.in_flight
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || memcmp(endpoint->work.identity, owner->start_payload, START(PORT_NO)) != 0
        || owner->initial.source_boot_id != endpoint->session.boot_id
        || !McuOpeningGate_ConfigurationMatches(owner, endpoint)
        || now < owner->accepted_at_ms || now < owner->initial_meta.observed_uptime_ms)
        return ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    clean = owner->start_message == ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION;
    duration = ecobin_uart_read_u32_be(owner->start_payload + (clean ? CLEAN(START_EXECUTION_WINDOW_MS) : START(START_EXECUTION_WINDOW_MS)));
    if (owner->accepted_at_ms > UINT64_MAX - duration) return ECOBIN_UART_NACK_ERROR_EXPIRED;
    limits.execution_deadline_ms = owner->accepted_at_ms + duration;
    if (now >= limits.execution_deadline_ms) return ECOBIN_UART_NACK_ERROR_EXPIRED;
    if (clean) {
        duration = ecobin_uart_read_u32_be(owner->start_payload + CLEAN(OPERATION_WINDOW_MS));
        if (owner->accepted_at_ms > UINT64_MAX - duration) return ECOBIN_UART_NACK_ERROR_EXPIRED;
        limits.operation_deadline_ms = owner->accepted_at_ms + duration;
        if (now >= limits.operation_deadline_ms) return ECOBIN_UART_NACK_ERROR_EXPIRED;
        limits.unlock_pulse_ms = ecobin_uart_read_u32_be(owner->configuration.active.preimage + DEVICE(CLEAN_SOLENOID_PULSE_MS));
    } else limits.delivery_auto_close_ms = ecobin_uart_read_u32_be(owner->start_payload + START(DELIVERY_AUTO_CLOSE_MS));
    *output = limits;
    return ECOBIN_UART_NACK_ERROR_NONE;
}

uint16_t McuOpeningGate_Evaluate(const McuWorkPreparation *owner,
    const McuControlEndpoint *endpoint, uint8_t message, const uint8_t *payload,
    size_t length, uint64_t received, uint64_t now, McuOpeningLimits *output) {
    (void)owner; (void)endpoint; (void)message; (void)payload;
    (void)length; (void)received; (void)now; (void)output;
    return ECOBIN_UART_NACK_ERROR_UNSUPPORTED_MESSAGE;
}
