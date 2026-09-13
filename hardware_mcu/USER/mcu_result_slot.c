#include "mcu_result_slot.h"
#include <string.h>

typedef char result_slot_ram_budget[(sizeof(McuResultSlot) <= 224u) ? 1 : -1];

void McuResultSlot_Init(McuResultSlot *slot, uint64_t boot_id) {
    memset(slot, 0, sizeof(*slot));
    if (boot_id <= UINT64_C(9007199254740991)) slot->boot_id = boot_id;
}

uint8_t McuResultSlot_Freeze(McuResultSlot *slot, const uint8_t *payload, size_t length) {
    uint32_t sequence;
    if (payload == NULL || length != ECOBIN_UART_WORK_RESULT_PAYLOAD_MAX_LENGTH
        || slot->boot_id == 0u) return 0u;
    if (ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_WORK_RESULT,
            payload, (uint16_t)length) != 0) return 0u;
    if (ecobin_uart_read_u64_be(payload + ECOBIN_UART_WORK_RESULT_MCU_BOOT_ID_OFFSET)
        != slot->boot_id) return 0u;
    if (slot->held) return (uint8_t)(memcmp(slot->payload, payload, length) == 0);
    sequence = ecobin_uart_read_u32_be(payload + ECOBIN_UART_WORK_RESULT_RESULT_SEQUENCE_OFFSET);
    if (sequence <= slot->highest_sequence) return 0u;
    memcpy(slot->payload, payload, length);
    slot->highest_sequence = sequence;
    slot->held = 1u;
    return 1u;
}

size_t McuResultSlot_CopyHeld(const McuResultSlot *slot, uint8_t *output, size_t capacity) {
    if (!slot->held || output == NULL || capacity < sizeof(slot->payload)) return 0u;
    memcpy(output, slot->payload, sizeof(slot->payload));
    return sizeof(slot->payload);
}

uint8_t McuResultSlot_Query(const McuResultSlot *slot, const uint8_t *identity, size_t length) {
    if (identity == NULL || length != ECOBIN_UART_RESULT_SAVED_PAYLOAD_MAX_LENGTH
        || ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_RESULT_SAVED,
            identity, (uint16_t)length) != 0) return 0u;
    if (ecobin_uart_read_u64_be(identity + ECOBIN_UART_RESULT_SAVED_MCU_BOOT_ID_OFFSET)
        != slot->boot_id) return ECOBIN_UART_RESULT_QUERY_STATUS_BOOT_MISMATCH;
    if (slot->highest_sequence == 0u || ecobin_uart_read_u32_be(identity
        + ECOBIN_UART_RESULT_SAVED_RESULT_SEQUENCE_OFFSET) != slot->highest_sequence)
        return ECOBIN_UART_RESULT_QUERY_STATUS_NOT_FOUND;
    if (memcmp(identity, slot->payload, length) != 0)
        return ECOBIN_UART_RESULT_QUERY_STATUS_IDENTITY_CONFLICT;
    return slot->held ? ECOBIN_UART_RESULT_QUERY_STATUS_HELD : ECOBIN_UART_RESULT_QUERY_STATUS_RELEASED;
}

uint8_t McuResultSlot_Saved(McuResultSlot *slot, const uint8_t *identity, size_t length) {
    uint8_t query_status = McuResultSlot_Query(slot, identity, length);
    switch (query_status) {
    case ECOBIN_UART_RESULT_QUERY_STATUS_HELD:
        slot->held = 0u;
        return ECOBIN_UART_RESULT_SAVED_STATUS_RELEASED;
    case ECOBIN_UART_RESULT_QUERY_STATUS_RELEASED:
        return ECOBIN_UART_RESULT_SAVED_STATUS_ALREADY_RELEASED;
    case ECOBIN_UART_RESULT_QUERY_STATUS_NOT_FOUND:
        return ECOBIN_UART_RESULT_SAVED_STATUS_NOT_FOUND;
    case ECOBIN_UART_RESULT_QUERY_STATUS_IDENTITY_CONFLICT:
        return ECOBIN_UART_RESULT_SAVED_STATUS_IDENTITY_CONFLICT;
    case ECOBIN_UART_RESULT_QUERY_STATUS_BOOT_MISMATCH:
        return ECOBIN_UART_RESULT_SAVED_STATUS_BOOT_MISMATCH;
    default:
        return 0u;
    }
}
