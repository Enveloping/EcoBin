#include "mcu_device_entry_url.h"
#include "mcu_control_endpoint.h"
#include <string.h>

typedef char device_entry_url_ram_budget[(sizeof(McuDeviceEntryUrl) <= 768u) ? 1 : -1];

#define BEGIN(field) ECOBIN_UART_DEVICE_ENTRY_URL_BEGIN_##field##_OFFSET
#define PART(field) ECOBIN_UART_DEVICE_ENTRY_URL_PART_##field##_OFFSET
#define COMMIT(field) ECOBIN_UART_DEVICE_ENTRY_URL_COMMIT_##field##_OFFSET
#define RESULT(field) ECOBIN_UART_DEVICE_ENTRY_URL_APPLY_RESULT_##field##_OFFSET

static void read_command(const uint8_t *payload, McuSessionCommand *command) {
    command->target_boot_id = ecobin_uart_read_u64_be(payload + BEGIN(TARGET_MCU_BOOT_ID));
    command->sequence = ecobin_uart_read_u32_be(payload + BEGIN(COMMAND_SEQUENCE));
    memcpy(command->uid, payload + BEGIN(MCU_COMMAND_UID), sizeof(command->uid));
    memcpy(command->digest, payload + BEGIN(COMMAND_DIGEST_SHA256), sizeof(command->digest));
}

static uint8_t same_command(const McuSessionCommand *left,
    const McuSessionCommand *right) {
    return (uint8_t)(left->target_boot_id == right->target_boot_id
        && left->sequence == right->sequence
        && memcmp(left->uid, right->uid, sizeof(left->uid)) == 0
        && memcmp(left->digest, right->digest, sizeof(left->digest)) == 0);
}

static uint8_t idle(const McuControlEndpoint *endpoint) {
    return (uint8_t)(endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        && endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RESULT_HELD
        && !endpoint->work.result.held);
}

static uint8_t safe_url(const uint8_t *url, uint16_t length) {
    uint16_t index;
    if (length < 8u || length > MCU_DEVICE_ENTRY_URL_MAX_LENGTH
        || memcmp(url, "https://", 8u) != 0) return 0u;
    for (index = 0u; index < length; ++index)
        if (url[index] < 0x21u || url[index] > 0x7eu
            || url[index] == 0x22u || url[index] == 0x5cu) return 0u;
    return 1u;
}

static uint8_t same_staging(const McuDeviceEntryUrl *state,
    const uint8_t *application, const uint8_t *digest, uint8_t part_count) {
    return (uint8_t)(state->staging_present
        && state->staging_part_count == part_count
        && memcmp(state->staging_application_uid, application, 16u) == 0
        && memcmp(state->staging_sha256, digest, 32u) == 0);
}

static void begin_staging(McuDeviceEntryUrl *state, const uint8_t *payload) {
    memset(state->staging_url, 0, sizeof(state->staging_url));
    memcpy(state->staging_application_uid, payload + BEGIN(APPLICATION_UID), 16u);
    memcpy(state->staging_sha256, payload + BEGIN(URL_SHA256), 32u);
    state->staging_length = ecobin_uart_read_u16_be(payload + BEGIN(URL_LENGTH));
    state->staging_part_count = payload[BEGIN(PART_COUNT)];
    state->staging_received = 0u;
    state->staging_next_part = 1u;
    state->staging_present = 1u;
}

static void append_part(McuDeviceEntryUrl *state, const uint8_t *payload,
    uint16_t chunk_length) {
    memcpy(state->staging_url + state->staging_received,
        payload + PART(URL_CHUNK) + 1u, chunk_length);
    state->staging_received = (uint16_t)(state->staging_received + chunk_length);
    ++state->staging_next_part;
}

static void cache_result(McuDeviceEntryUrl *state, const McuSessionCommand *command,
    uint32_t event_sequence, uint64_t now_ms, uint8_t status, uint16_t error) {
    memset(state->result, 0, sizeof(state->result));
    ecobin_uart_write_u64_be(state->result + RESULT(MCU_BOOT_ID), state->boot_id);
    ecobin_uart_write_u32_be(state->result + RESULT(MCU_EVENT_SEQUENCE), event_sequence);
    ecobin_uart_write_u64_be(state->result + RESULT(UPTIME_MS), now_ms);
    memcpy(state->result + RESULT(MCU_COMMAND_UID), command->uid, sizeof(command->uid));
    memcpy(state->result + RESULT(APPLICATION_UID), state->staging_application_uid, 16u);
    ecobin_uart_write_u16_be(state->result + RESULT(URL_LENGTH), state->staging_length);
    memcpy(state->result + RESULT(URL_SHA256), state->staging_sha256, 32u);
    state->result[RESULT(STATUS)] = status;
    ecobin_uart_write_u16_be(state->result + RESULT(ERROR_CODE), error);
    state->result_command = *command;
    state->result_present = 1u;
}

void McuDeviceEntryUrl_Init(McuDeviceEntryUrl *state,
    McuDeviceEntryUrlWriter writer, void *context) {
    if (state == NULL) return;
    memset(state, 0, sizeof(*state));
    state->writer = writer;
    state->writer_context = context;
}

void McuDeviceEntryUrl_Bind(McuDeviceEntryUrl *state, uint64_t boot_id) {
    McuDeviceEntryUrlWriter writer;
    void *context;
    if (state == NULL) return;
    writer = state->writer;
    context = state->writer_context;
    memset(state, 0, sizeof(*state));
    state->writer = writer;
    state->writer_context = context;
    state->boot_id = boot_id;
}

uint8_t McuDeviceEntryUrl_Receive(McuDeviceEntryUrl *state,
    McuControlEndpoint *endpoint, uint16_t owner_error, uint8_t message,
    const uint8_t *payload, size_t length, uint64_t now_ms,
    McuSessionDecision *decision) {
    McuSessionCommand command;
    uint8_t digest[32];
    uint16_t error = owner_error, chunk_length = 0u, expected_chunk = 0u;
    uint32_t event_sequence = 0u;
    if (state == NULL || endpoint == NULL || payload == NULL || decision == NULL
        || state->writer == NULL || owner_error > ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT
        || (message != ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_BEGIN
            && message != ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_PART
            && message != ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_COMMIT)
        || ecobin_uart_validate_session_payload(message, payload, (uint16_t)length) != 0) return 0u;
    read_command(payload, &command);
    if (!McuSession_QueryCommand(&endpoint->session, &command, decision)) return 0u;
    if (decision->outcome != ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN) return 1u;
    if (error == ECOBIN_UART_NACK_ERROR_NONE && (!idle(endpoint)
        || state->boot_id != endpoint->session.boot_id)) error = ECOBIN_UART_NACK_ERROR_BUSY;
    if (error == ECOBIN_UART_NACK_ERROR_NONE && message == ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_BEGIN) {
        if ((state->active_present && memcmp(state->active_application_uid,
                payload + BEGIN(APPLICATION_UID), 16u) == 0)
            || (state->staging_present && memcmp(state->staging_application_uid,
                payload + BEGIN(APPLICATION_UID), 16u) == 0))
            error = ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    } else if (error == ECOBIN_UART_NACK_ERROR_NONE && message == ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_PART) {
        chunk_length = payload[PART(URL_CHUNK)];
        if (!same_staging(state, payload + PART(APPLICATION_UID),
                payload + PART(URL_SHA256), payload[PART(PART_COUNT)])
            || payload[PART(PART_INDEX)] != state->staging_next_part
            || state->staging_received >= state->staging_length)
            error = ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
        else {
            expected_chunk = (uint16_t)(state->staging_length - state->staging_received);
            if (expected_chunk > 64u) expected_chunk = 64u;
            if (chunk_length != expected_chunk) error = ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
        }
    } else if (error == ECOBIN_UART_NACK_ERROR_NONE) {
        if (!same_staging(state, payload + COMMIT(APPLICATION_UID),
                payload + COMMIT(URL_SHA256), payload[COMMIT(PART_COUNT)])
            || ecobin_uart_read_u16_be(payload + COMMIT(URL_LENGTH)) != state->staging_length
            || state->staging_received != state->staging_length
            || state->staging_next_part != (uint8_t)(state->staging_part_count + 1u))
            error = ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
        else {
            ecobin_uart_sha256(state->staging_url, state->staging_length, digest);
            if (memcmp(digest, state->staging_sha256, sizeof(digest)) != 0
                || !safe_url(state->staging_url, state->staging_length))
                error = ECOBIN_UART_NACK_ERROR_INVALID_FIELD;
            else {
                event_sequence = McuControlEndpoint_ReserveEventSequence(endpoint);
                if (event_sequence == 0u) error = ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT;
            }
        }
    }
    if (!McuSession_ReceiveCommand(&endpoint->session, &command, error, decision)) return 0u;
    if (!decision->execute_once) return 1u;
    if (message == ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_BEGIN) begin_staging(state, payload);
    else if (message == ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_PART)
        append_part(state, payload, chunk_length);
    else if (state->writer(state->staging_url, state->staging_length, state->writer_context)) {
        memcpy(state->active_url, state->staging_url, state->staging_length);
        if (state->staging_length < sizeof(state->active_url))
            memset(state->active_url + state->staging_length, 0,
                sizeof(state->active_url) - state->staging_length);
        memcpy(state->active_application_uid, state->staging_application_uid, 16u);
        memcpy(state->active_sha256, state->staging_sha256, 32u);
        state->active_length = state->staging_length;
        state->active_present = 1u;
        cache_result(state, &command, event_sequence, now_ms,
            ECOBIN_UART_DEVICE_ENTRY_URL_APPLY_STATUS_APPLIED, ECOBIN_UART_NACK_ERROR_NONE);
        state->staging_present = 0u;
    } else cache_result(state, &command, event_sequence, now_ms,
        ECOBIN_UART_DEVICE_ENTRY_URL_APPLY_STATUS_FAILED, ECOBIN_UART_NACK_ERROR_BUSY);
    return 1u;
}

size_t McuDeviceEntryUrl_CopyResult(const McuDeviceEntryUrl *state,
    const McuSessionCommand *command, uint8_t *message,
    uint8_t *output, size_t capacity) {
    if (state == NULL || command == NULL || message == NULL || output == NULL
        || capacity < sizeof(state->result) || !state->result_present
        || !same_command(command, &state->result_command)) return 0u;
    memcpy(output, state->result, sizeof(state->result));
    *message = ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_APPLY_RESULT;
    return sizeof(state->result);
}

uint8_t McuDeviceEntryUrl_IsStaging(const McuDeviceEntryUrl *state) {
    return (uint8_t)(state != NULL && state->staging_present);
}
