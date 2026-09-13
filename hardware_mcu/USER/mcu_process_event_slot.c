#include "mcu_process_event_slot.h"
#include <string.h>

typedef char process_slot_ram_budget[(sizeof(McuProcessEventSlot) <= 416u) ? 1 : -1];
typedef char process_fullness_body_fits[(ECOBIN_UART_FULLNESS_SAMPLE_RESULT_PAYLOAD_MAX_LENGTH <= MCU_PROCESS_EVENT_BODY_CAPACITY) ? 1 : -1];
typedef char process_clean_body_fits[(ECOBIN_UART_CLEAN_FINAL_WEIGHT_READY_PAYLOAD_MAX_LENGTH <= MCU_PROCESS_EVENT_BODY_CAPACITY) ? 1 : -1];
#define SCOPE(field) (ECOBIN_UART_QUERY_PROCESS_EVENT_##field##_OFFSET - 8u)
#define REPLY(field) ECOBIN_UART_PROCESS_EVENT_QUERY_REPLY_##field##_OFFSET
#define SAVED(field) ECOBIN_UART_PROCESS_EVENT_SAVED_##field##_OFFSET

#define MATCH_COMMON(message, subject) \
    if (memcmp(scope + SCOPE(WORK_UID), payload + ECOBIN_UART_##message##_##subject##_OFFSET, 16u) != 0 \
        || scope[SCOPE(PORT_NO)] != payload[ECOBIN_UART_##message##_PORT_NO_OFFSET] \
        || memcmp(scope + SCOPE(CONFIG_VERSION), payload + ECOBIN_UART_##message##_CONFIG_VERSION_OFFSET, 8u) != 0) return 0u
#define MATCH_COMMAND(message) \
    if (memcmp(scope + SCOPE(MCU_COMMAND_UID), payload + ECOBIN_UART_##message##_MCU_COMMAND_UID_OFFSET, 16u) != 0) return 0u
#define MATCH_STEP(message, field) \
    if (memcmp(scope + SCOPE(STEP_SEQUENCE), payload + ECOBIN_UART_##message##_##field##_OFFSET, 2u) != 0) return 0u

static uint8_t scope_matches(const uint8_t *scope, uint8_t message_type, const uint8_t *payload) {
    switch (message_type) {
    case ECOBIN_UART_MESSAGE_WORK_PREOPEN_WEIGHT_READY:
        MATCH_COMMON(WORK_PREOPEN_WEIGHT_READY, SESSION_UID);
        MATCH_COMMAND(WORK_PREOPEN_WEIGHT_READY);
        MATCH_STEP(WORK_PREOPEN_WEIGHT_READY, ROUND_INDEX);
        break;
    case ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY:
        MATCH_COMMON(WORK_POSTCLOSE_WEIGHT_READY, SESSION_UID);
        MATCH_COMMAND(WORK_POSTCLOSE_WEIGHT_READY);
        MATCH_STEP(WORK_POSTCLOSE_WEIGHT_READY, ROUND_INDEX);
        break;
    case ECOBIN_UART_MESSAGE_DELIVERY_SELECTION:
        MATCH_COMMON(DELIVERY_SELECTION, SESSION_UID);
        MATCH_COMMAND(DELIVERY_SELECTION);
        MATCH_STEP(DELIVERY_SELECTION, ROUND_INDEX);
        break;
    case ECOBIN_UART_MESSAGE_WORK_PREUNLOCK_WEIGHT_READY:
        MATCH_COMMON(WORK_PREUNLOCK_WEIGHT_READY, OPERATION_UID);
        MATCH_COMMAND(WORK_PREUNLOCK_WEIGHT_READY);
        break;
    case ECOBIN_UART_MESSAGE_CLEAN_UNLOCK_REQUESTED:
        MATCH_COMMON(CLEAN_UNLOCK_REQUESTED, OPERATION_UID);
        MATCH_COMMAND(CLEAN_UNLOCK_REQUESTED);
        MATCH_STEP(CLEAN_UNLOCK_REQUESTED, CLEAN_ACTION_SEQUENCE);
        break;
    case ECOBIN_UART_MESSAGE_CLEAN_FINISH_REQUESTED:
        MATCH_COMMON(CLEAN_FINISH_REQUESTED, OPERATION_UID);
        MATCH_COMMAND(CLEAN_FINISH_REQUESTED);
        MATCH_STEP(CLEAN_FINISH_REQUESTED, CLEAN_ACTION_SEQUENCE);
        break;
    case ECOBIN_UART_MESSAGE_CLEAN_FINAL_WEIGHT_READY:
        MATCH_COMMON(CLEAN_FINAL_WEIGHT_READY, OPERATION_UID);
        MATCH_STEP(CLEAN_FINAL_WEIGHT_READY, CLEAN_ACTION_SEQUENCE);
        break;
    case ECOBIN_UART_MESSAGE_CLEAN_COMPLETION_CONFIRMED:
        MATCH_COMMON(CLEAN_COMPLETION_CONFIRMED, OPERATION_UID);
        MATCH_COMMAND(CLEAN_COMPLETION_CONFIRMED);
        MATCH_STEP(CLEAN_COMPLETION_CONFIRMED, CLEAN_ACTION_SEQUENCE);
        break;
    case ECOBIN_UART_MESSAGE_FULLNESS_SAMPLE_RESULT:
        MATCH_COMMON(FULLNESS_SAMPLE_RESULT, DETECTION_UID);
        MATCH_COMMAND(FULLNESS_SAMPLE_RESULT);
        break;
    case ECOBIN_UART_MESSAGE_BASELINE_MEASUREMENT_RESULT:
        MATCH_COMMON(BASELINE_MEASUREMENT_RESULT, MEASUREMENT_UID);
        MATCH_COMMAND(BASELINE_MEASUREMENT_RESULT);
        break;
    default: return 0u;
    }
    return 1u;
}

static uint8_t selection_follows_saved_weight(const McuProcessEventSlot *slot,
    const uint8_t *scope, const uint8_t *payload) {
    uint8_t kind;
    if (slot->held || slot->highest_sequence == 0u
        || slot->scope[SCOPE(EVENT_MESSAGE_TYPE)] != ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY
        || slot->length != ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_PAYLOAD_MAX_LENGTH
        || memcmp(slot->scope, scope, SCOPE(EVENT_MESSAGE_TYPE)) != 0
        || memcmp(slot->scope + SCOPE(STEP_SEQUENCE), scope + SCOPE(STEP_SEQUENCE),
            sizeof(slot->scope) - SCOPE(STEP_SEQUENCE)) != 0
        || memcmp(slot->payload + ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_MEASUREMENT_UID_OFFSET,
            payload + ECOBIN_UART_DELIVERY_SELECTION_POST_CLOSE_MEASUREMENT_UID_OFFSET, 16u) != 0
        || ecobin_uart_read_u64_be(payload + ECOBIN_UART_DELIVERY_SELECTION_UPTIME_MS_OFFSET)
            < ecobin_uart_read_u64_be(slot->payload + ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_UPTIME_MS_OFFSET)) return 0u;
    kind = slot->payload[ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_MEASUREMENT_KIND_OFFSET];
    return (uint8_t)(kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_STABLE_MEAN
        || kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_TIMEOUT_MEDIAN);
}

static uint8_t confirmation_follows_saved_final(const McuProcessEventSlot *slot,
    const uint8_t *scope, const uint8_t *payload) {
    /* A terminal unavailable weight is still an explicit candidate; human
     * confirmation does not turn it into a valid measurement. */
    return (uint8_t)(!slot->held && slot->highest_sequence != 0u
        && slot->scope[SCOPE(EVENT_MESSAGE_TYPE)] == ECOBIN_UART_MESSAGE_CLEAN_FINAL_WEIGHT_READY
        && slot->length == ECOBIN_UART_CLEAN_FINAL_WEIGHT_READY_PAYLOAD_MAX_LENGTH
        && memcmp(slot->scope, scope, SCOPE(EVENT_MESSAGE_TYPE)) == 0
        && memcmp(slot->scope + SCOPE(STEP_SEQUENCE), scope + SCOPE(STEP_SEQUENCE),
            sizeof(slot->scope) - SCOPE(STEP_SEQUENCE)) == 0
        && memcmp(slot->payload + ECOBIN_UART_CLEAN_FINAL_WEIGHT_READY_MEASUREMENT_UID_OFFSET,
            payload + ECOBIN_UART_CLEAN_COMPLETION_CONFIRMED_FINAL_MEASUREMENT_UID_OFFSET, 16u) == 0
        && ecobin_uart_read_u64_be(payload + ECOBIN_UART_CLEAN_COMPLETION_CONFIRMED_UPTIME_MS_OFFSET)
            >= ecobin_uart_read_u64_be(slot->payload + ECOBIN_UART_CLEAN_FINAL_WEIGHT_READY_UPTIME_MS_OFFSET));
}

void McuProcessEventSlot_Init(McuProcessEventSlot *slot, uint64_t boot_id) {
    memset(slot, 0, sizeof(*slot));
    if (boot_id <= UINT64_C(9007199254740991)) slot->boot_id = boot_id;
}

uint8_t McuProcessEventSlot_Freeze(McuProcessEventSlot *slot, const uint8_t *scope,
    size_t scope_length, uint8_t message_type, const uint8_t *payload, size_t length) {
    uint8_t request[ECOBIN_UART_QUERY_PROCESS_EVENT_PAYLOAD_MAX_LENGTH];
    uint32_t sequence;
    if (scope == NULL || payload == NULL || scope_length != sizeof(slot->scope)
        || length > sizeof(slot->payload) || slot->boot_id == 0u) return 0u;
    memset(request, 0, 8u);
    request[7] = 1u; /* Local shape validation only; not a transmitted query ID. */
    memcpy(request + 8u, scope, scope_length);
    if (ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_QUERY_PROCESS_EVENT, request, sizeof(request)) != 0
        || scope[SCOPE(EVENT_MESSAGE_TYPE)] != message_type
        || ecobin_uart_validate_session_payload(message_type, payload, (uint16_t)length) != 0
        || ecobin_uart_read_u64_be(scope + SCOPE(TARGET_MCU_BOOT_ID)) != slot->boot_id
        || ecobin_uart_read_u64_be(payload) != slot->boot_id
        || !scope_matches(scope, message_type, payload)) return 0u;
    if (slot->held) return (uint8_t)(slot->length == length
        && memcmp(slot->scope, scope, scope_length) == 0 && memcmp(slot->payload, payload, length) == 0);
    sequence = ecobin_uart_read_u32_be(payload + ECOBIN_UART_WORK_PREOPEN_WEIGHT_READY_MCU_EVENT_SEQUENCE_OFFSET);
    if (sequence <= slot->highest_sequence || (slot->highest_sequence != 0u
        && memcmp(slot->scope, scope, scope_length) == 0)) return 0u;
    if (message_type == ECOBIN_UART_MESSAGE_DELIVERY_SELECTION
        && !selection_follows_saved_weight(slot, scope, payload)) return 0u;
    if (message_type == ECOBIN_UART_MESSAGE_CLEAN_COMPLETION_CONFIRMED
        && !confirmation_follows_saved_final(slot, scope, payload)) return 0u;
    memcpy(slot->scope, scope, scope_length);
    memcpy(slot->payload, payload, length);
    slot->length = (uint16_t)length;
    slot->highest_sequence = sequence;
    ecobin_uart_write_u64_be(slot->identity + SAVED(MCU_BOOT_ID), slot->boot_id);
    ecobin_uart_write_u32_be(slot->identity + SAVED(MCU_EVENT_SEQUENCE), sequence);
    slot->identity[SAVED(EVENT_MESSAGE_TYPE)] = message_type;
    ecobin_uart_compute_process_event_digest(message_type, payload, (uint16_t)length,
        slot->identity + SAVED(EVENT_DIGEST_SHA256));
    slot->held = 1u;
    return 1u;
}

size_t McuProcessEventSlot_CopyHeld(const McuProcessEventSlot *slot, uint8_t *output, size_t capacity) {
    if (!slot->held || output == NULL || capacity < slot->length) return 0u;
    memcpy(output, slot->payload, slot->length);
    return slot->length;
}

size_t McuProcessEventSlot_Query(const McuProcessEventSlot *slot, const uint8_t *request,
    size_t length, uint8_t *reply, size_t capacity) {
    uint8_t status;
    const uint8_t *scope;
    if (request == NULL || reply == NULL || length != ECOBIN_UART_QUERY_PROCESS_EVENT_PAYLOAD_MAX_LENGTH
        || capacity < ECOBIN_UART_PROCESS_EVENT_QUERY_REPLY_PAYLOAD_MAX_LENGTH
        || ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_QUERY_PROCESS_EVENT, request, (uint16_t)length) != 0) return 0u;
    scope = request + 8u;
    if (ecobin_uart_read_u64_be(scope + SCOPE(TARGET_MCU_BOOT_ID)) != slot->boot_id)
        status = ECOBIN_UART_RESULT_QUERY_STATUS_BOOT_MISMATCH;
    else if (slot->highest_sequence != 0u && memcmp(scope, slot->scope, sizeof(slot->scope)) != 0
        && memcmp(scope + SCOPE(WORK_UID), slot->scope + SCOPE(WORK_UID), 16u) == 0
        && scope[SCOPE(EVENT_MESSAGE_TYPE)] == slot->scope[SCOPE(EVENT_MESSAGE_TYPE)]
        && memcmp(scope + SCOPE(STEP_SEQUENCE), slot->scope + SCOPE(STEP_SEQUENCE), 2u) == 0)
        status = ECOBIN_UART_RESULT_QUERY_STATUS_IDENTITY_CONFLICT;
    else if (slot->highest_sequence == 0u || memcmp(scope, slot->scope, sizeof(slot->scope)) != 0)
        status = ECOBIN_UART_RESULT_QUERY_STATUS_NOT_FOUND;
    else status = slot->held ? ECOBIN_UART_RESULT_QUERY_STATUS_HELD : ECOBIN_UART_RESULT_QUERY_STATUS_RELEASED;
    memmove(reply, request, length);
    memset(reply + length, 0, ECOBIN_UART_PROCESS_EVENT_QUERY_REPLY_PAYLOAD_MAX_LENGTH - length);
    ecobin_uart_write_u64_be(reply + REPLY(CURRENT_MCU_BOOT_ID), slot->boot_id);
    reply[REPLY(STATUS)] = status;
    if (status == ECOBIN_UART_RESULT_QUERY_STATUS_HELD || status == ECOBIN_UART_RESULT_QUERY_STATUS_RELEASED) {
        ecobin_uart_write_u32_be(reply + REPLY(MCU_EVENT_SEQUENCE), slot->highest_sequence);
        memcpy(reply + REPLY(EVENT_DIGEST_SHA256), slot->identity + SAVED(EVENT_DIGEST_SHA256), 32u);
    }
    return ECOBIN_UART_PROCESS_EVENT_QUERY_REPLY_PAYLOAD_MAX_LENGTH;
}

uint8_t McuProcessEventSlot_Saved(McuProcessEventSlot *slot, const uint8_t *identity, size_t length) {
    if (identity == NULL || length != sizeof(slot->identity)
        || ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_PROCESS_EVENT_SAVED, identity, (uint16_t)length) != 0) return 0u;
    if (ecobin_uart_read_u64_be(identity + SAVED(MCU_BOOT_ID)) != slot->boot_id)
        return ECOBIN_UART_RESULT_SAVED_STATUS_BOOT_MISMATCH;
    if (slot->highest_sequence == 0u || ecobin_uart_read_u32_be(identity + SAVED(MCU_EVENT_SEQUENCE)) != slot->highest_sequence)
        return ECOBIN_UART_RESULT_SAVED_STATUS_NOT_FOUND;
    if (memcmp(identity, slot->identity, length) != 0) return ECOBIN_UART_RESULT_SAVED_STATUS_IDENTITY_CONFLICT;
    if (!slot->held) return ECOBIN_UART_RESULT_SAVED_STATUS_ALREADY_RELEASED;
    slot->held = 0u;
    return ECOBIN_UART_RESULT_SAVED_STATUS_RELEASED;
}
