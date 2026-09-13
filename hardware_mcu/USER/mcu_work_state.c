#include "mcu_work_state.h"
#include <string.h>

#define ID_OFFSET(field) (ECOBIN_UART_QUERY_WORK_##field##_OFFSET - ECOBIN_UART_QUERY_WORK_MCU_COMMAND_UID_OFFSET)
typedef char work_state_ram_budget[(sizeof(McuWorkState) <= 304u) ? 1 : -1];

static uint8_t running_phase(uint8_t work, uint8_t phase) {
    if (phase == ECOBIN_UART_MCU_WORK_PHASE_SAFETY_LOCKED) return 1u;
    if (work == ECOBIN_UART_WORK_TYPE_DELIVERY_SESSION)
        return (uint8_t)(phase >= ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_PREPARING
            && phase <= ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_FINALIZING);
    if (work == ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION)
        return (uint8_t)(phase >= ECOBIN_UART_MCU_WORK_PHASE_CLEAN_PREPARING
            && phase <= ECOBIN_UART_MCU_WORK_PHASE_CLEAN_RECOVERY_REQUIRED);
    return 0u;
}

void McuWorkState_Init(McuWorkState *state, uint64_t boot_id) {
    memset(state, 0, sizeof(*state));
    McuResultSlot_Init(&state->result, boot_id);
}

uint8_t McuWorkState_CanBegin(const McuWorkState *state, const uint8_t *identity, size_t length, uint8_t phase) {
    uint8_t request[ECOBIN_UART_QUERY_WORK_PAYLOAD_MAX_LENGTH];
    if (state == NULL || identity == NULL || length != sizeof(state->identity) || state->result.boot_id == 0u
        || state->status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING || state->result.held) return 0u;
    ecobin_uart_write_u64_be(request, 1u); /* shape validation, never transmitted */
    memcpy(request + ECOBIN_UART_QUERY_WORK_MCU_COMMAND_UID_OFFSET, identity, length);
    if (ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_QUERY_WORK, request, sizeof(request)) != 0
        || ecobin_uart_read_u64_be(identity + ID_OFFSET(TARGET_MCU_BOOT_ID)) != state->result.boot_id
        || !running_phase(identity[ID_OFFSET(WORK_TYPE)], phase)) return 0u;
    if (state->status != 0u && (
        ecobin_uart_read_u32_be(identity + ID_OFFSET(COMMAND_SEQUENCE)) <=
            ecobin_uart_read_u32_be(state->identity + ID_OFFSET(COMMAND_SEQUENCE))
        || memcmp(identity + ID_OFFSET(MCU_COMMAND_UID), state->identity + ID_OFFSET(MCU_COMMAND_UID), 16u) == 0
        || memcmp(identity + ID_OFFSET(WORK_UID), state->identity + ID_OFFSET(WORK_UID), 16u) == 0)) return 0u;
    return 1u;
}

uint8_t McuWorkState_BeginAccepted(McuWorkState *state, const uint8_t *identity, size_t length, uint8_t phase) {
    if (!McuWorkState_CanBegin(state, identity, length, phase)) return 0u;
    memcpy(state->identity, identity, length);
    state->status = ECOBIN_UART_WORK_QUERY_STATUS_RUNNING;
    state->phase = phase;
    return 1u;
}

uint8_t McuWorkState_SetPhase(McuWorkState *state, uint8_t phase) {
    if (state->status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || !running_phase(state->identity[ID_OFFSET(WORK_TYPE)], phase)) return 0u;
    state->phase = phase;
    return 1u;
}

uint8_t McuWorkState_Complete(McuWorkState *state, const uint8_t *payload, size_t length) {
    if (payload == NULL || length != ECOBIN_UART_WORK_RESULT_PAYLOAD_MAX_LENGTH) return 0u;
    if (state->status == ECOBIN_UART_WORK_QUERY_STATUS_RESULT_HELD)
        return (uint8_t)(memcmp(state->result.payload, payload, length) == 0);
    if (state->status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || memcmp(payload + ECOBIN_UART_WORK_RESULT_WORK_UID_OFFSET, state->identity + ID_OFFSET(WORK_UID), 16u) != 0
        || memcmp(payload + ECOBIN_UART_WORK_RESULT_ORIGIN_COMMAND_UID_OFFSET, state->identity + ID_OFFSET(MCU_COMMAND_UID), 16u) != 0
        || memcmp(payload + ECOBIN_UART_WORK_RESULT_ORIGIN_COMMAND_SEQUENCE_OFFSET, state->identity + ID_OFFSET(COMMAND_SEQUENCE), 4u) != 0
        || payload[ECOBIN_UART_WORK_RESULT_WORK_TYPE_OFFSET] != state->identity[ID_OFFSET(WORK_TYPE)]
        || payload[ECOBIN_UART_WORK_RESULT_PORT_NO_OFFSET] != state->identity[ID_OFFSET(PORT_NO)]
        || !McuResultSlot_Freeze(&state->result, payload, length)) return 0u;
    state->status = ECOBIN_UART_WORK_QUERY_STATUS_RESULT_HELD;
    state->phase = state->identity[ID_OFFSET(WORK_TYPE)] == ECOBIN_UART_WORK_TYPE_DELIVERY_SESSION
        ? ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_FINALIZING : ECOBIN_UART_MCU_WORK_PHASE_CLEAN_FINALIZING;
    return 1u;
}

uint8_t McuWorkState_Saved(McuWorkState *state, const uint8_t *identity, size_t length) {
    uint8_t status = McuResultSlot_Saved(&state->result, identity, length);
    if (status == ECOBIN_UART_RESULT_SAVED_STATUS_RELEASED
        && state->status == ECOBIN_UART_WORK_QUERY_STATUS_RESULT_HELD)
        state->status = ECOBIN_UART_WORK_QUERY_STATUS_RESULT_RELEASED;
    return status;
}

size_t McuWorkState_CopyHeld(const McuWorkState *state, uint8_t *output, size_t capacity) {
    return McuResultSlot_CopyHeld(&state->result, output, capacity);
}

size_t McuWorkState_Query(const McuWorkState *state, const uint8_t *request, size_t length,
                         uint8_t *output, size_t capacity) {
    const uint8_t *identity;
    uint8_t status = ECOBIN_UART_WORK_QUERY_STATUS_NOT_FOUND;
    if (output == NULL || capacity < ECOBIN_UART_WORK_QUERY_REPLY_PAYLOAD_MAX_LENGTH
        || request == NULL || length != ECOBIN_UART_QUERY_WORK_PAYLOAD_MAX_LENGTH
        || ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_QUERY_WORK, request, (uint16_t)length) != 0) return 0u;
    identity = request + ECOBIN_UART_QUERY_WORK_MCU_COMMAND_UID_OFFSET;
    if (ecobin_uart_read_u64_be(identity + ID_OFFSET(TARGET_MCU_BOOT_ID)) != state->result.boot_id)
        status = ECOBIN_UART_WORK_QUERY_STATUS_BOOT_MISMATCH;
    else if (state->status != 0u) {
        if (memcmp(identity, state->identity, sizeof(state->identity)) == 0) status = state->status;
        else if (memcmp(identity + ID_OFFSET(COMMAND_SEQUENCE), state->identity + ID_OFFSET(COMMAND_SEQUENCE), 4u) == 0
            || memcmp(identity + ID_OFFSET(MCU_COMMAND_UID), state->identity + ID_OFFSET(MCU_COMMAND_UID), 16u) == 0
            || memcmp(identity + ID_OFFSET(WORK_UID), state->identity + ID_OFFSET(WORK_UID), 16u) == 0)
            status = ECOBIN_UART_WORK_QUERY_STATUS_IDENTITY_CONFLICT;
    }
    memset(output, 0, ECOBIN_UART_WORK_QUERY_REPLY_PAYLOAD_MAX_LENGTH);
    memcpy(output, request, length);
    ecobin_uart_write_u64_be(output + ECOBIN_UART_WORK_QUERY_REPLY_CURRENT_MCU_BOOT_ID_OFFSET, state->result.boot_id);
    output[ECOBIN_UART_WORK_QUERY_REPLY_STATUS_OFFSET] = status;
    if (status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING || status == ECOBIN_UART_WORK_QUERY_STATUS_RESULT_HELD
        || status == ECOBIN_UART_WORK_QUERY_STATUS_RESULT_RELEASED) output[ECOBIN_UART_WORK_QUERY_REPLY_PHASE_OFFSET] = state->phase;
    if (status == ECOBIN_UART_WORK_QUERY_STATUS_RESULT_HELD || status == ECOBIN_UART_WORK_QUERY_STATUS_RESULT_RELEASED) {
        memcpy(output + ECOBIN_UART_WORK_QUERY_REPLY_RESULT_SEQUENCE_OFFSET,
               state->result.payload + ECOBIN_UART_WORK_RESULT_RESULT_SEQUENCE_OFFSET, 4u);
        memcpy(output + ECOBIN_UART_WORK_QUERY_REPLY_RESULT_DIGEST_SHA256_OFFSET,
               state->result.payload + ECOBIN_UART_WORK_RESULT_RESULT_DIGEST_SHA256_OFFSET, 32u);
    }
    return ECOBIN_UART_WORK_QUERY_REPLY_PAYLOAD_MAX_LENGTH;
}
