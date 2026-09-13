#include "mcu_configuration.h"
#include <string.h>

typedef char configuration_ram_budget[(sizeof(McuConfiguration) <= 1536u) ? 1 : -1];

static uint8_t same_settings(const McuConfigCollection *bank, const uint8_t *payload) {
    return memcmp(bank->preimage + ECOBIN_UART_CONFIG_PREIMAGE_CONTENT_SHA256_OFFSET,
        payload + ECOBIN_UART_CONFIG_BEGIN_CONTENT_SHA256_OFFSET, 32u) == 0
        && memcmp(bank->expected_digest, payload + ECOBIN_UART_CONFIG_BEGIN_MCU_PAYLOAD_SHA256_OFFSET, 32u) == 0;
}

static uint16_t prepare_begin(McuConfiguration *state, const uint8_t *payload) {
    const McuConfigCollection *bank;
    uint8_t index;
    uint64_t incoming = ecobin_uart_read_u64_be(payload + ECOBIN_UART_CONFIG_BEGIN_CONFIG_VERSION_OFFSET), prior;
    for (index = 0u; index < 2u; ++index) {
        bank = index == 0u ? &state->active : &state->staging;
        if (bank->received_parts == 0u) continue;
        prior = ecobin_uart_read_u64_be(bank->preimage + ECOBIN_UART_CONFIG_PREIMAGE_VERSION_OFFSET);
        if (incoming < prior || (incoming == prior && !same_settings(bank, payload)))
            return ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
        if (memcmp(bank->application_uid, payload + ECOBIN_UART_CONFIG_BEGIN_APPLICATION_UID_OFFSET, 16u) == 0
            && incoming != prior) return ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    }
    if (state->staging.received_parts != 0u
        && memcmp(state->staging.application_uid, payload + ECOBIN_UART_CONFIG_BEGIN_APPLICATION_UID_OFFSET, 16u) != 0)
        McuConfigCollection_Init(&state->trial, state->staging.boot_id, state->staging.port_capacity);
    return ECOBIN_UART_NACK_ERROR_NONE;
}

void McuConfiguration_Init(McuConfiguration *state, uint64_t boot_id, uint8_t port_count) {
    McuConfigCollection_Init(&state->active, boot_id, port_count);
    McuConfigCollection_Init(&state->staging, boot_id, port_count);
    McuConfigCollection_Init(&state->trial, boot_id, port_count);
}

uint8_t McuConfiguration_Receive(McuConfiguration *state, McuSession *session,
    const McuWorkState *work, uint16_t owner_error, uint8_t message,
    const uint8_t *payload, size_t length, McuSessionDecision *decision) {
    McuSessionCommand command;
    uint8_t offered;
    uint16_t error = owner_error;
    if (decision == NULL) return 0u;
    decision->execute_once = 0u;
    if (state == NULL || session == NULL || work == NULL || payload == NULL
        || owner_error > ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT || length > ECOBIN_UART_MAX_PAYLOAD_LENGTH
        || (message != ECOBIN_UART_MESSAGE_CONFIG_BEGIN && message != ECOBIN_UART_MESSAGE_CONFIG_DEVICE_BLOCK
            && message != ECOBIN_UART_MESSAGE_CONFIG_PORT_BLOCK && message != ECOBIN_UART_MESSAGE_CONFIG_COMMIT)
        || ecobin_uart_validate_session_payload(message, payload, (uint16_t)length) != 0) return 0u;
    command.target_boot_id = ecobin_uart_read_u64_be(payload + ECOBIN_UART_CONFIG_BEGIN_TARGET_MCU_BOOT_ID_OFFSET);
    command.sequence = ecobin_uart_read_u32_be(payload + ECOBIN_UART_CONFIG_BEGIN_COMMAND_SEQUENCE_OFFSET);
    memcpy(command.uid, payload + ECOBIN_UART_CONFIG_BEGIN_MCU_COMMAND_UID_OFFSET, sizeof(command.uid));
    memcpy(command.digest, payload + ECOBIN_UART_CONFIG_BEGIN_COMMAND_DIGEST_SHA256_OFFSET, sizeof(command.digest));
    if (!McuSession_QueryCommand(session, &command, decision)) return 0u;
    if (decision->outcome != ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN) return 1u;
    if (error == ECOBIN_UART_NACK_ERROR_NONE) {
        if (state->staging.boot_id != session->boot_id || work->result.boot_id != session->boot_id)
            error = ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
        else if (work->status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
            || work->status == ECOBIN_UART_WORK_QUERY_STATUS_RESULT_HELD || work->result.held)
            error = ECOBIN_UART_NACK_ERROR_BUSY;
        else {
            state->trial = state->staging;
            if (message == ECOBIN_UART_MESSAGE_CONFIG_BEGIN) error = prepare_begin(state, payload);
            if (error == ECOBIN_UART_NACK_ERROR_NONE) {
                offered = McuConfigCollection_Offer(&state->trial, message, payload, length);
                if (offered != MCU_CONFIG_COLLECTION_STAGED && offered != MCU_CONFIG_COLLECTION_COMPLETE
                    && offered != MCU_CONFIG_COLLECTION_DUPLICATE)
                    error = offered == MCU_CONFIG_COLLECTION_CONFLICT || offered == MCU_CONFIG_COLLECTION_INCOMPLETE
                        ? ECOBIN_UART_NACK_ERROR_STATE_CONFLICT : ECOBIN_UART_NACK_ERROR_INVALID_FIELD;
            }
        }
    }
    if (!McuSession_ReceiveCommand(session, &command, error, decision)) return 0u;
    if (decision->execute_once) {
        state->staging = state->trial;
        if (state->staging.complete) state->active = state->staging;
    }
    return 1u;
}

size_t McuConfiguration_CopyActive(const McuConfiguration *state, uint8_t *output, size_t capacity) {
    return McuConfigCollection_CopyComplete(state == NULL ? NULL : &state->active, output, capacity);
}

uint8_t McuConfiguration_ReadWeightPolicy(const McuConfiguration *state, uint8_t port_no, McuConfigWeightPolicy *output) {
    return McuConfigCollection_ReadWeightPolicy(state == NULL ? NULL : &state->active, port_no, output);
}

uint8_t McuConfiguration_IsStaging(const McuConfiguration *state) {
    return state != NULL && state->staging.received_parts != 0u && !state->staging.complete;
}

uint8_t McuConfiguration_ReadFullnessPolicy(const McuConfiguration *state, uint8_t port_no, McuConfigFullnessPolicy *output) {
    return McuConfigCollection_ReadFullnessPolicy(state == NULL ? NULL : &state->active, port_no, output);
}
