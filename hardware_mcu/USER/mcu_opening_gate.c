#include "mcu_opening_gate.h"
#include <string.h>

#define START(field) ECOBIN_UART_START_DELIVERY_SESSION_##field##_OFFSET
#define OPEN(field) ECOBIN_UART_AUTHORIZE_DELIVERY_FIRST_OPEN_##field##_OFFSET
#define UNLOCK(field) ECOBIN_UART_UNLOCK_CLEAN_DOOR_##field##_OFFSET
#define CLEAN_START(field) ECOBIN_UART_START_CLEAN_OPERATION_##field##_OFFSET
#define EVENT(field) ECOBIN_UART_WORK_PREOPEN_WEIGHT_READY_##field##_OFFSET
#define CLEAN_EVENT(field) ECOBIN_UART_WORK_PREUNLOCK_WEIGHT_READY_##field##_OFFSET
#define SCOPE(field) (ECOBIN_UART_QUERY_PROCESS_EVENT_##field##_OFFSET - 8u)
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

/* The retained released slot is proof of the EXACT SAVED exchange, not merely
 * an empty mailbox. Slot identity/digest/body were validated by Freeze/Saved.
 * Never discard this evidence before the eventual execution owner consumes it.
 */
uint8_t McuOpeningGate_InitialSaved(const McuWorkPreparation *owner, const McuControlEndpoint *endpoint, uint8_t clean) {
    const McuProcessEventSlot *slot = &endpoint->process_event;
    const McuResultMeasurement *initial = &owner->initial;
    uint8_t message = clean ? ECOBIN_UART_MESSAGE_WORK_PREUNLOCK_WEIGHT_READY : ECOBIN_UART_MESSAGE_WORK_PREOPEN_WEIGHT_READY;
    return (uint8_t)(!slot->held && slot->boot_id == endpoint->session.boot_id
        && slot->highest_sequence == initial->event_sequence && initial->event_sequence != 0u
        && slot->length == (clean ? ECOBIN_UART_WORK_PREUNLOCK_WEIGHT_READY_PAYLOAD_MAX_LENGTH : ECOBIN_UART_WORK_PREOPEN_WEIGHT_READY_PAYLOAD_MAX_LENGTH)
        && memcmp(slot->scope, endpoint->work.identity, MCU_WORK_IDENTITY_LENGTH) == 0
        && slot->scope[SCOPE(EVENT_MESSAGE_TYPE)] == message
        && ecobin_uart_read_u16_be(slot->scope + SCOPE(STEP_SEQUENCE)) == (clean ? 0u : 1u)
        && ecobin_uart_read_u64_be(slot->scope + SCOPE(CONFIG_VERSION)) == owner->initial_meta.config_version
        && ecobin_uart_read_u64_be(slot->identity) == initial->source_boot_id
        && ecobin_uart_read_u32_be(slot->identity + 8u) == initial->event_sequence
        && slot->identity[12u] == message
        && memcmp(slot->payload + (clean ? CLEAN_EVENT(MEASUREMENT_UID) : EVENT(MEASUREMENT_UID)), initial->uid, sizeof(initial->uid)) == 0
        && slot->payload[clean ? CLEAN_EVENT(MEASUREMENT_KIND) : EVENT(MEASUREMENT_KIND)] == initial->kind
        && ecobin_uart_read_u64_be(slot->payload + EVENT(UPTIME_MS)) == owner->initial_meta.observed_uptime_ms);
}

/* Checked absolute deadlines: a grant can shorten, never renew, local START. */
static uint8_t deadline(uint64_t origin, uint32_t duration, uint64_t *output) {
    if (duration == 0u || origin > UINT64_MAX - duration) return 0u;
    *output = origin + duration;
    return 1u;
}

uint16_t McuOpeningGate_Evaluate(const McuWorkPreparation *owner,
    const McuControlEndpoint *endpoint, uint8_t message, const uint8_t *payload,
    size_t length, uint64_t received_ms, uint64_t now_ms, McuOpeningLimits *output) {
    const uint8_t *start;
    McuOpeningLimits limits;
    uint64_t grant_deadline;
    uint16_t error;
    uint8_t clean;
    if (owner == NULL || endpoint == NULL || payload == NULL || output == NULL)
        return ECOBIN_UART_NACK_ERROR_INVALID_FIELD;
    if (message != ECOBIN_UART_MESSAGE_AUTHORIZE_DELIVERY_FIRST_OPEN && message != ECOBIN_UART_MESSAGE_UNLOCK_CLEAN_DOOR)
        return ECOBIN_UART_NACK_ERROR_UNSUPPORTED_MESSAGE;
    clean = message == ECOBIN_UART_MESSAGE_UNLOCK_CLEAN_DOOR;
    if (length > ECOBIN_UART_MAX_PAYLOAD_LENGTH
        || ecobin_uart_validate_session_payload(message, payload, (uint16_t)length) != 0)
        return ECOBIN_UART_NACK_ERROR_INVALID_FIELD;
    if (endpoint->application_context != owner || owner->guard == NULL || endpoint->session.boot_id == 0u
        || ecobin_uart_read_u64_be(payload + OPEN(TARGET_MCU_BOOT_ID)) != endpoint->session.boot_id)
        return ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    start = owner->start_payload;
    if (owner->start_message != (clean ? ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION : ECOBIN_UART_MESSAGE_START_DELIVERY_SESSION)
        || ecobin_uart_validate_session_payload(owner->start_message, start, owner->start_length) != 0
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || endpoint->work.result.boot_id != endpoint->session.boot_id
        || memcmp(endpoint->work.identity, start, START(PORT_NO)) != 0
        || endpoint->work.identity[MCU_WORK_IDENTITY_LENGTH - 2u] != (clean ? ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION : ECOBIN_UART_WORK_TYPE_DELIVERY_SESSION)
        || endpoint->work.identity[MCU_WORK_IDENTITY_LENGTH - 1u] != start[START(PORT_NO)]
        || memcmp(payload + OPEN(SESSION_UID), start + START(SESSION_UID), 16u) != 0
        || payload[OPEN(PORT_NO)] != start[START(PORT_NO)] || payload[OPEN(PORT_NO)] != endpoint->facts.port_no
        || memcmp(payload + (clean ? UNLOCK(PARENT_COMMAND_UID) : OPEN(PARENT_START_COMMAND_UID)), start + START(MCU_COMMAND_UID), 16u) != 0)
        return ECOBIN_UART_NACK_ERROR_UNKNOWN_WORK;
    /* A nonzero action belongs to an actual HMI request, and a recovered work
     * belongs to RESUME. Neither may masquerade as a first unlock here. */
    if (clean && (ecobin_uart_read_u16_be(payload + UNLOCK(CLEAN_ACTION_SEQUENCE)) != 0u
        || ecobin_uart_read_u32_be(payload + UNLOCK(RECOVERY_GENERATION)) != 0u))
        return ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    if (ecobin_uart_read_u32_be(payload + OPEN(COMMAND_SEQUENCE)) <= endpoint->session.highest_sequence
        || memcmp(payload + OPEN(MCU_COMMAND_UID), start + START(MCU_COMMAND_UID), 16u) == 0
        || memcmp(payload + OPEN(MCU_COMMAND_UID), endpoint->session.latest_command.uid, 16u) == 0)
        return ECOBIN_UART_NACK_ERROR_IDEMPOTENCY_CONFLICT;
    if (endpoint->process_event.held || endpoint->work.result.held
        || (owner->weight.present && !owner->weight.retired) || owner->weight.in_flight)
        return ECOBIN_UART_NACK_ERROR_BUSY;
    if (!owner->initial_ready || !owner->weight.retired
        || endpoint->work.phase != (clean ? ECOBIN_UART_MCU_WORK_PHASE_CLEAN_WAIT_FIRST_UNLOCK : ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_FIRST_OPEN_AUTH)
        || (owner->initial.kind != ECOBIN_UART_RESULT_MEASUREMENT_KIND_STABLE_MEAN
            && owner->initial.kind != ECOBIN_UART_RESULT_MEASUREMENT_KIND_TIMEOUT_MEDIAN)
        || owner->initial.source_boot_id != endpoint->session.boot_id
        || owner->initial_meta.step_sequence != (clean ? 0u : 1u)
        || (!clean && memcmp(payload + OPEN(FIRST_PRE_OPEN_MEASUREMENT_UID), owner->initial.uid, 16u) != 0)
        || !McuOpeningGate_ConfigurationMatches(owner, endpoint) || !McuOpeningGate_InitialSaved(owner, endpoint, clean))
        return ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    if (now_ms < received_ms || received_ms < owner->accepted_at_ms
        || received_ms < owner->initial_meta.observed_uptime_ms || now_ms < endpoint->last_input_ms
        || now_ms < owner->weight.last_now_ms)
        return ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    memset(&limits, 0, sizeof(limits));
    if (!deadline(owner->accepted_at_ms, ecobin_uart_read_u32_be(start + (clean ? CLEAN_START(START_EXECUTION_WINDOW_MS) : START(START_EXECUTION_WINDOW_MS))), &limits.execution_deadline_ms)
        || !deadline(received_ms, ecobin_uart_read_u32_be(payload + (clean ? UNLOCK(REMAINING_OPERATION_WINDOW_MS) : OPEN(REMAINING_START_AUTHORIZATION_MS))), &grant_deadline))
        return ECOBIN_UART_NACK_ERROR_EXPIRED;
    if (clean) {
        if (!deadline(owner->accepted_at_ms, ecobin_uart_read_u32_be(start + CLEAN_START(OPERATION_WINDOW_MS)), &limits.operation_deadline_ms))
            return ECOBIN_UART_NACK_ERROR_EXPIRED;
        if (grant_deadline < limits.operation_deadline_ms) limits.operation_deadline_ms = grant_deadline;
        grant_deadline = limits.operation_deadline_ms;
        limits.unlock_pulse_ms = ecobin_uart_read_u32_be(owner->configuration.active.preimage + DEVICE(CLEAN_SOLENOID_PULSE_MS));
        if (ecobin_uart_read_u32_be(payload + UNLOCK(UNLOCK_PULSE_MS)) != limits.unlock_pulse_ms)
            return ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    } else {
        limits.delivery_auto_close_ms = ecobin_uart_read_u32_be(start + START(DELIVERY_AUTO_CLOSE_MS));
    }
    if (grant_deadline < limits.execution_deadline_ms) limits.execution_deadline_ms = grant_deadline;
    if (now_ms >= limits.execution_deadline_ms) return ECOBIN_UART_NACK_ERROR_EXPIRED;
    error = owner->guard(message, payload, length, now_ms, owner->guard_context);
    if (error > ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT) error = ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT;
    if (error != ECOBIN_UART_NACK_ERROR_NONE) return error;
    *output = limits;
    return ECOBIN_UART_NACK_ERROR_NONE;
}
