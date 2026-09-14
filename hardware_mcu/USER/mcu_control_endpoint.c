#include "mcu_control_endpoint.h"
#include "firmware_identity.h"
#include <string.h>

typedef char endpoint_ram_budget[(sizeof(McuControlEndpoint) <= 3072u) ? 1 : -1];
typedef char firmware_version_budget[
    ((sizeof(ECOBIN_MCU_FIRMWARE_VERSION) - 1u) >= 5u
        && (sizeof(ECOBIN_MCU_FIRMWARE_VERSION) - 1u) <= 32u) ? 1 : -1];

static const uint8_t firmware_identity[8] = ECOBIN_MCU_FIRMWARE_IDENTITY_BYTES;

static void emit(McuControlEndpoint *endpoint, uint8_t message, uint16_t length) {
    size_t encoded = 0u;
    uint8_t flags = 0u;
    if (ecobin_uart_validate_session_payload(message, endpoint->payload, length) != 0) return;
    switch (message) {
    case ECOBIN_UART_MESSAGE_WORK_PREOPEN_WEIGHT_READY:
    case ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY:
    case ECOBIN_UART_MESSAGE_DELIVERY_SELECTION:
    case ECOBIN_UART_MESSAGE_CLEAN_UNLOCK_REQUESTED:
    case ECOBIN_UART_MESSAGE_CLEAN_FINISH_REQUESTED:
    case ECOBIN_UART_MESSAGE_CLEAN_COMPLETION_CONFIRMED:
    case ECOBIN_UART_MESSAGE_WORK_PREUNLOCK_WEIGHT_READY:
    case ECOBIN_UART_MESSAGE_CLEAN_FINAL_WEIGHT_READY:
    case ECOBIN_UART_MESSAGE_FULLNESS_SAMPLE_RESULT:
    case ECOBIN_UART_MESSAGE_BASELINE_MEASUREMENT_RESULT:
    case ECOBIN_UART_MESSAGE_DELIVERY_DOOR_COMMAND_RESULT:
    case ECOBIN_UART_MESSAGE_DELIVERY_LOCAL_DOOR_RESULT:
    case ECOBIN_UART_MESSAGE_DELIVERY_CYCLE_ABORTED:
    case ECOBIN_UART_MESSAGE_DELIVERY_POSTCLOSE_INTERRUPTED:
    case ECOBIN_UART_MESSAGE_CLEAN_LOCK_POWER_CHANGED:
    case ECOBIN_UART_MESSAGE_CLEAN_OPERATION_INTERRUPTED:
    case ECOBIN_UART_MESSAGE_SAFE_CLOSE_RESULT:
        flags = ECOBIN_UART_FLAG_ACK_REQUIRED;
        break;
    default: break;
    }
    endpoint->tx_sequence = endpoint->tx_sequence == UINT32_MAX ? 1u : endpoint->tx_sequence + 1u;
    if (ecobin_uart_encode_frame(message, flags, endpoint->tx_sequence, endpoint->payload, length,
        endpoint->transmit, sizeof(endpoint->transmit), &encoded) == 0)
        endpoint->sink(endpoint->transmit, encoded, endpoint->sink_context);
}

static void emit_command_result(McuControlEndpoint *endpoint,
    const McuSessionCommand *command) {
    uint8_t message = 0u;
    size_t length;
    /* A held application result has its own exact command identity.  Replay it
     * even after the one-entry session decision cache has advanced and the
     * generic query outcome is OLD_DETAILS_UNAVAILABLE. */
    if (endpoint->command_result_handler == NULL) return;
    length = endpoint->command_result_handler(command, &message, endpoint->payload,
        sizeof(endpoint->payload), endpoint->application_context);
    if (length != 0u) emit(endpoint, message, (uint16_t)length);
}

static void receive(const uint8_t *frame, size_t length, const ecobin_uart_frame_view_t *view, void *context) {
    McuControlEndpoint *endpoint = (McuControlEndpoint *)context;
    const uint8_t *input = view->payload;
    uint64_t boot;
    McuSessionBindingReply binding;
    McuSessionCommand command;
    McuSessionDecision decision;
    size_t reply_length;
    uint8_t result_status;
    (void)frame; (void)length;
    switch (view->message_type) {
    case ECOBIN_UART_MESSAGE_BOOT_PROBE:
        if (!McuSession_Probe(&endpoint->session, ecobin_uart_read_u64_be(input), &boot)) return;
        memcpy(endpoint->payload, input, ECOBIN_UART_BOOT_PROBE_PAYLOAD_MAX_LENGTH);
        ecobin_uart_write_u64_be(endpoint->payload + ECOBIN_UART_BOOT_PROBE_REPLY_MCU_BOOT_ID_OFFSET, boot);
        emit(endpoint, ECOBIN_UART_MESSAGE_BOOT_PROBE_REPLY, ECOBIN_UART_BOOT_PROBE_REPLY_PAYLOAD_MAX_LENGTH);
        break;
    case ECOBIN_UART_MESSAGE_BIND_BOOT:
        if (!McuSession_Bind(&endpoint->session, ecobin_uart_read_u64_be(input),
            ecobin_uart_read_u64_be(input + ECOBIN_UART_BIND_BOOT_PROPOSED_MCU_BOOT_ID_OFFSET), &binding)) return;
        if (binding.status == ECOBIN_UART_BOOT_BIND_STATUS_BOUND) {
            endpoint->critical_event_sequence = 0u;
            McuWorkState_Init(&endpoint->work, binding.current_boot_id);
            McuProcessEventSlot_Init(&endpoint->process_event, binding.current_boot_id);
            McuActuatorEventJournal_Init(&endpoint->actuator_events, binding.current_boot_id);
            if (endpoint->bound_handler != NULL) endpoint->bound_handler(endpoint, endpoint->application_context);
        }
        memcpy(endpoint->payload, input, ECOBIN_UART_BIND_BOOT_PAYLOAD_MAX_LENGTH);
        ecobin_uart_write_u64_be(endpoint->payload + ECOBIN_UART_BIND_BOOT_REPLY_MCU_BOOT_ID_OFFSET, binding.current_boot_id);
        endpoint->payload[ECOBIN_UART_BIND_BOOT_REPLY_STATUS_OFFSET] = binding.status;
        emit(endpoint, ECOBIN_UART_MESSAGE_BIND_BOOT_REPLY, ECOBIN_UART_BIND_BOOT_REPLY_PAYLOAD_MAX_LENGTH);
        break;
    case ECOBIN_UART_MESSAGE_QUERY_COMMAND:
        command.target_boot_id = ecobin_uart_read_u64_be(input + ECOBIN_UART_QUERY_COMMAND_TARGET_MCU_BOOT_ID_OFFSET);
        command.sequence = ecobin_uart_read_u32_be(input + ECOBIN_UART_QUERY_COMMAND_COMMAND_SEQUENCE_OFFSET);
        memcpy(command.uid, input + ECOBIN_UART_QUERY_COMMAND_MCU_COMMAND_UID_OFFSET, sizeof(command.uid));
        memcpy(command.digest, input + ECOBIN_UART_QUERY_COMMAND_COMMAND_DIGEST_SHA256_OFFSET, sizeof(command.digest));
        if (!McuSession_QueryCommand(&endpoint->session, &command, &decision)) return;
        memcpy(endpoint->payload, input, ECOBIN_UART_QUERY_COMMAND_PAYLOAD_MAX_LENGTH);
        ecobin_uart_write_u64_be(endpoint->payload + ECOBIN_UART_COMMAND_QUERY_RESULT_CURRENT_MCU_BOOT_ID_OFFSET, decision.current_boot_id);
        endpoint->payload[ECOBIN_UART_COMMAND_QUERY_RESULT_OUTCOME_OFFSET] = decision.outcome;
        ecobin_uart_write_u16_be(endpoint->payload + ECOBIN_UART_COMMAND_QUERY_RESULT_ERROR_CODE_OFFSET, decision.error_code);
        ecobin_uart_write_u32_be(endpoint->payload + ECOBIN_UART_COMMAND_QUERY_RESULT_HIGHEST_COMMAND_SEQUENCE_OFFSET, decision.highest_sequence);
        emit(endpoint, ECOBIN_UART_MESSAGE_COMMAND_QUERY_RESULT, ECOBIN_UART_COMMAND_QUERY_RESULT_PAYLOAD_MAX_LENGTH);
        emit_command_result(endpoint, &command);
        break;
    case ECOBIN_UART_MESSAGE_QUERY_PROCESS_EVENT:
        reply_length = McuProcessEventSlot_Query(&endpoint->process_event, input,
            view->payload_length, endpoint->payload, sizeof(endpoint->payload));
        if (!reply_length) return;
        result_status = endpoint->payload[ECOBIN_UART_PROCESS_EVENT_QUERY_REPLY_STATUS_OFFSET];
        emit(endpoint, ECOBIN_UART_MESSAGE_PROCESS_EVENT_QUERY_REPLY, (uint16_t)reply_length);
        if (result_status == ECOBIN_UART_RESULT_QUERY_STATUS_HELD) {
            reply_length = McuProcessEventSlot_CopyHeld(&endpoint->process_event, endpoint->payload, sizeof(endpoint->payload));
            if (reply_length) emit(endpoint, input[ECOBIN_UART_QUERY_PROCESS_EVENT_EVENT_MESSAGE_TYPE_OFFSET], (uint16_t)reply_length);
        }
        break;
    case ECOBIN_UART_MESSAGE_QUERY_ACTUATOR_EVENT:
        reply_length = McuActuatorEventJournal_Query(&endpoint->actuator_events, input,
            view->payload_length, endpoint->payload, sizeof(endpoint->payload));
        if (!reply_length) return;
        result_status = endpoint->payload[ECOBIN_UART_ACTUATOR_EVENT_QUERY_REPLY_STATUS_OFFSET];
        emit(endpoint, ECOBIN_UART_MESSAGE_ACTUATOR_EVENT_QUERY_REPLY, (uint16_t)reply_length);
        if (result_status == ECOBIN_UART_ACTUATOR_EVENT_QUERY_STATUS_HELD) {
            reply_length = McuActuatorEventJournal_CopyNextHeld(&endpoint->actuator_events,
                ecobin_uart_read_u32_be(input + ECOBIN_UART_QUERY_ACTUATOR_EVENT_AFTER_MCU_EVENT_SEQUENCE_OFFSET),
                &result_status, endpoint->payload, sizeof(endpoint->payload));
            if (reply_length) emit(endpoint, result_status, (uint16_t)reply_length);
        }
        break;
    case ECOBIN_UART_MESSAGE_ACTUATOR_EVENT_SAVED:
        result_status = McuActuatorEventJournal_Saved(&endpoint->actuator_events, input, view->payload_length);
        if (!result_status) return;
        memcpy(endpoint->payload, input, ECOBIN_UART_ACTUATOR_EVENT_SAVED_PAYLOAD_MAX_LENGTH);
        ecobin_uart_write_u64_be(endpoint->payload + ECOBIN_UART_ACTUATOR_EVENT_SAVED_REPLY_CURRENT_MCU_BOOT_ID_OFFSET,
            endpoint->session.boot_id);
        endpoint->payload[ECOBIN_UART_ACTUATOR_EVENT_SAVED_REPLY_STATUS_OFFSET] = result_status;
        emit(endpoint, ECOBIN_UART_MESSAGE_ACTUATOR_EVENT_SAVED_REPLY, ECOBIN_UART_ACTUATOR_EVENT_SAVED_REPLY_PAYLOAD_MAX_LENGTH);
        break;
    case ECOBIN_UART_MESSAGE_PROCESS_EVENT_SAVED:
        result_status = McuProcessEventSlot_Saved(&endpoint->process_event, input, view->payload_length);
        if (!result_status) return;
        memcpy(endpoint->payload, input, ECOBIN_UART_PROCESS_EVENT_SAVED_PAYLOAD_MAX_LENGTH);
        ecobin_uart_write_u64_be(endpoint->payload + ECOBIN_UART_PROCESS_EVENT_SAVED_REPLY_CURRENT_MCU_BOOT_ID_OFFSET,
            endpoint->session.boot_id);
        endpoint->payload[ECOBIN_UART_PROCESS_EVENT_SAVED_REPLY_STATUS_OFFSET] = result_status;
        emit(endpoint, ECOBIN_UART_MESSAGE_PROCESS_EVENT_SAVED_REPLY, ECOBIN_UART_PROCESS_EVENT_SAVED_REPLY_PAYLOAD_MAX_LENGTH);
        break;
    case ECOBIN_UART_MESSAGE_QUERY_WORK:
        reply_length = McuWorkState_Query(&endpoint->work, input, view->payload_length, endpoint->payload, sizeof(endpoint->payload));
        if (reply_length) emit(endpoint, ECOBIN_UART_MESSAGE_WORK_QUERY_REPLY, (uint16_t)reply_length);
        break;
    case ECOBIN_UART_MESSAGE_QUERY_DEVICE_FACTS:
        reply_length = McuDeviceFacts_Capture(&endpoint->facts, &endpoint->work,
            input, view->payload_length, endpoint->payload, sizeof(endpoint->payload));
        if (reply_length) emit(endpoint, ECOBIN_UART_MESSAGE_DEVICE_FACTS_REPLY, (uint16_t)reply_length);
        break;
    case ECOBIN_UART_MESSAGE_QUERY_DEVICE_IDENTITY:
        memcpy(endpoint->payload, input, ECOBIN_UART_QUERY_DEVICE_IDENTITY_PAYLOAD_MAX_LENGTH);
        ecobin_uart_write_u64_be(endpoint->payload
            + ECOBIN_UART_DEVICE_IDENTITY_REPLY_CURRENT_MCU_BOOT_ID_OFFSET,
            endpoint->session.boot_id);
        endpoint->payload[ECOBIN_UART_DEVICE_IDENTITY_REPLY_STATUS_OFFSET] =
            ecobin_uart_read_u64_be(input
                + ECOBIN_UART_QUERY_DEVICE_IDENTITY_TARGET_MCU_BOOT_ID_OFFSET)
                    == endpoint->session.boot_id
                ? ECOBIN_UART_DEVICE_IDENTITY_STATUS_AVAILABLE
                : ECOBIN_UART_DEVICE_IDENTITY_STATUS_BOOT_MISMATCH;
        endpoint->payload[ECOBIN_UART_DEVICE_IDENTITY_REPLY_PROTOCOL_MAJOR_OFFSET] = 2u;
        endpoint->payload[ECOBIN_UART_DEVICE_IDENTITY_REPLY_PROTOCOL_MINOR_OFFSET] = 0u;
        endpoint->payload[ECOBIN_UART_DEVICE_IDENTITY_REPLY_PORT_COUNT_OFFSET] = 1u;
        ecobin_uart_write_u64_be(endpoint->payload
            + ECOBIN_UART_DEVICE_IDENTITY_REPLY_CAPABILITY_BITMAP_OFFSET,
            ECOBIN_UART_CAPABILITY_CONFIG_STAGING_COMMIT
                | ECOBIN_UART_CAPABILITY_DEVICE_ENTRY_URL_APPLICATION);
        ecobin_uart_write_u32_be(endpoint->payload
            + ECOBIN_UART_DEVICE_IDENTITY_REPLY_HIGHEST_COMMAND_SEQUENCE_OFFSET,
            endpoint->session.highest_sequence);
        ecobin_uart_write_u32_be(endpoint->payload
            + ECOBIN_UART_DEVICE_IDENTITY_REPLY_FIRMWARE_VERSION_CODE_OFFSET,
            (uint32_t)ECOBIN_MCU_FIRMWARE_VERSION_CODE);
        ecobin_uart_write_u32_be(endpoint->payload
            + ECOBIN_UART_DEVICE_IDENTITY_REPLY_FIRMWARE_IDENTITY_HIGH_OFFSET,
            ecobin_uart_read_u32_be(firmware_identity));
        ecobin_uart_write_u32_be(endpoint->payload
            + ECOBIN_UART_DEVICE_IDENTITY_REPLY_FIRMWARE_IDENTITY_LOW_OFFSET,
            ecobin_uart_read_u32_be(firmware_identity + 4u));
        endpoint->payload[ECOBIN_UART_DEVICE_IDENTITY_REPLY_FIRMWARE_VERSION_OFFSET] =
            (uint8_t)(sizeof(ECOBIN_MCU_FIRMWARE_VERSION) - 1u);
        memcpy(endpoint->payload
                + ECOBIN_UART_DEVICE_IDENTITY_REPLY_FIRMWARE_VERSION_OFFSET + 1u,
            ECOBIN_MCU_FIRMWARE_VERSION,
            sizeof(ECOBIN_MCU_FIRMWARE_VERSION) - 1u);
        emit(endpoint, ECOBIN_UART_MESSAGE_DEVICE_IDENTITY_REPLY,
            (uint16_t)(ECOBIN_UART_DEVICE_IDENTITY_REPLY_PAYLOAD_MIN_LENGTH
                + sizeof(ECOBIN_MCU_FIRMWARE_VERSION) - 1u));
        break;
    case ECOBIN_UART_MESSAGE_QUERY_RESULT:
        result_status = McuResultSlot_Query(&endpoint->work.result,
            input + ECOBIN_UART_QUERY_RESULT_MCU_BOOT_ID_OFFSET, ECOBIN_UART_RESULT_SAVED_PAYLOAD_MAX_LENGTH);
        if (!result_status) return;
        memcpy(endpoint->payload, input, ECOBIN_UART_QUERY_RESULT_PAYLOAD_MAX_LENGTH);
        ecobin_uart_write_u64_be(endpoint->payload + ECOBIN_UART_RESULT_QUERY_REPLY_CURRENT_MCU_BOOT_ID_OFFSET,
            endpoint->session.boot_id);
        endpoint->payload[ECOBIN_UART_RESULT_QUERY_REPLY_STATUS_OFFSET] = result_status;
        emit(endpoint, ECOBIN_UART_MESSAGE_RESULT_QUERY_REPLY, ECOBIN_UART_RESULT_QUERY_REPLY_PAYLOAD_MAX_LENGTH);
        if (result_status == ECOBIN_UART_RESULT_QUERY_STATUS_HELD) {
            reply_length = McuWorkState_CopyHeld(&endpoint->work, endpoint->payload, sizeof(endpoint->payload));
            if (reply_length) emit(endpoint, ECOBIN_UART_MESSAGE_WORK_RESULT, (uint16_t)reply_length);
        }
        break;
    case ECOBIN_UART_MESSAGE_RESULT_SAVED:
        result_status = McuWorkState_Saved(&endpoint->work, input, view->payload_length);
        if (!result_status) return;
        memcpy(endpoint->payload, input, ECOBIN_UART_RESULT_SAVED_PAYLOAD_MAX_LENGTH);
        ecobin_uart_write_u64_be(endpoint->payload + ECOBIN_UART_RESULT_SAVED_REPLY_CURRENT_MCU_BOOT_ID_OFFSET,
            endpoint->session.boot_id);
        endpoint->payload[ECOBIN_UART_RESULT_SAVED_REPLY_STATUS_OFFSET] = result_status;
        emit(endpoint, ECOBIN_UART_MESSAGE_RESULT_SAVED_REPLY, ECOBIN_UART_RESULT_SAVED_REPLY_PAYLOAD_MAX_LENGTH);
        break;
    default:
        if (endpoint->command_handler != NULL
            && (view->message_type == ECOBIN_UART_MESSAGE_SAFE_CLOSE
                || (view->message_type >= ECOBIN_UART_MESSAGE_CONFIG_BEGIN && view->message_type <= ECOBIN_UART_MESSAGE_CONFIG_COMMIT)
                || (view->message_type >= ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_BEGIN
                    && view->message_type <= ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_COMMIT)
                || (view->message_type >= ECOBIN_UART_MESSAGE_START_DELIVERY_SESSION && view->message_type <= ECOBIN_UART_MESSAGE_CONFIRM_NO_ACTIVE_WORK))
            && endpoint->command_handler(endpoint, view->message_type, input, view->payload_length,
                endpoint->last_input_ms, endpoint->application_context, &decision)) {
            memcpy(endpoint->payload, input, ECOBIN_UART_COMMAND_DECISION_CURRENT_MCU_BOOT_ID_OFFSET);
            ecobin_uart_write_u64_be(endpoint->payload + ECOBIN_UART_COMMAND_DECISION_CURRENT_MCU_BOOT_ID_OFFSET, decision.current_boot_id);
            endpoint->payload[ECOBIN_UART_COMMAND_DECISION_OUTCOME_OFFSET] = decision.outcome;
            ecobin_uart_write_u16_be(endpoint->payload + ECOBIN_UART_COMMAND_DECISION_ERROR_CODE_OFFSET, decision.error_code);
            emit(endpoint, ECOBIN_UART_MESSAGE_COMMAND_DECISION, ECOBIN_UART_COMMAND_DECISION_PAYLOAD_MAX_LENGTH);
            emit_command_result(endpoint, &decision.command);
            break;
        }
        /* No application handler accepted this message: no invented decision. */
        endpoint->parser.diagnostics |= ECOBIN_UART_DIAG_SEMANTIC_REJECTED;
        break;
    }
}

uint8_t McuControlEndpoint_AttachCommandResults(McuControlEndpoint *endpoint,
    McuControlCommandResultHandler results) {
    if (endpoint == NULL || results == NULL || endpoint->feeding
        || endpoint->session.boot_id != 0u || endpoint->command_handler == NULL
        || endpoint->command_result_handler != NULL) return 0u;
    endpoint->command_result_handler = results;
    return 1u;
}

uint8_t McuControlEndpoint_AttachCommands(McuControlEndpoint *endpoint,
    McuControlCommandHandler commands, McuControlBoundHandler bound, void *context) {
    if (endpoint == NULL || endpoint->sink == NULL || commands == NULL || bound == NULL || context == NULL
        || endpoint->feeding || endpoint->session.boot_id != 0u || endpoint->command_handler != NULL) return 0u;
    endpoint->command_handler = commands;
    endpoint->bound_handler = bound;
    endpoint->application_context = context;
    return 1u;
}

uint32_t McuControlEndpoint_ReserveEventSequence(McuControlEndpoint *endpoint) {
    if (endpoint == NULL || endpoint->session.boot_id == 0u
        || UINT32_MAX - endpoint->critical_event_sequence <= McuActuatorEventJournal_PendingCount(&endpoint->actuator_events)) return 0u;
    return ++endpoint->critical_event_sequence;
}

uint8_t McuControlEndpoint_ReserveActuatorEvents(McuControlEndpoint *endpoint,
    uint8_t count, McuActuatorEventReservation *reservation) {
    uint32_t remaining;
    uint8_t pending;
    if (endpoint == NULL || endpoint->session.boot_id == 0u) return 0u;
    remaining = UINT32_MAX - endpoint->critical_event_sequence;
    pending = McuActuatorEventJournal_PendingCount(&endpoint->actuator_events);
    if (remaining < pending || remaining - pending < count) return 0u;
    return McuActuatorEventJournal_Reserve(&endpoint->actuator_events, count, reservation);
}

uint32_t McuControlEndpoint_PublishActuatorEvent(McuControlEndpoint *endpoint,
    const McuActuatorEventReservation *reservation, uint8_t member,
    uint8_t message_type, const uint8_t *payload_template, size_t length) {
    if (endpoint == NULL || endpoint->session.boot_id == 0u) return 0u;
    return McuActuatorEventJournal_PublishNext(&endpoint->actuator_events, reservation,
        member, message_type, payload_template, length, &endpoint->critical_event_sequence);
}

uint8_t McuControlEndpoint_CancelActuatorEvents(McuControlEndpoint *endpoint,
    const McuActuatorEventReservation *reservation) {
    if (endpoint == NULL) return 0u;
    return McuActuatorEventJournal_Cancel(&endpoint->actuator_events, reservation);
}

uint8_t McuControlEndpoint_ConfirmActuatorEventSaved(McuControlEndpoint *endpoint,
    uint8_t message_type, const uint8_t *payload, size_t length) {
    if (endpoint == NULL) return 0u;
    return McuActuatorEventJournal_ConfirmSaved(&endpoint->actuator_events, message_type, payload, length);
}

size_t McuControlEndpoint_CopyNextActuatorEvent(const McuControlEndpoint *endpoint,
    uint32_t after_sequence, uint8_t *message_type, uint8_t *output, size_t capacity) {
    if (endpoint == NULL) return 0u;
    return McuActuatorEventJournal_CopyNextHeld(&endpoint->actuator_events,
        after_sequence, message_type, output, capacity);
}

void McuControlEndpoint_Init(McuControlEndpoint *endpoint, uint8_t port_no, McuControlSink sink, void *context) {
    memset(endpoint, 0, sizeof(*endpoint));
    McuSession_Init(&endpoint->session);
    McuWorkState_Init(&endpoint->work, 0u);
    McuProcessEventSlot_Init(&endpoint->process_event, 0u);
    McuActuatorEventJournal_Init(&endpoint->actuator_events, 0u);
    McuDeviceFacts_Init(&endpoint->facts, port_no);
    ecobin_uart_stream_parser_init(&endpoint->parser, ECOBIN_UART_SENDER_ROLE_EDGE);
    endpoint->sink = sink;
    endpoint->sink_context = context;
}

size_t McuControlEndpoint_Feed(McuControlEndpoint *endpoint, const uint8_t *data, size_t length, uint64_t now_ms) {
    size_t parsed;
    if (endpoint == NULL || endpoint->sink == NULL || endpoint->feeding
        || (data == NULL && length != 0u) || now_ms < endpoint->last_input_ms) return 0u;
    endpoint->feeding = 1u;
    endpoint->last_input_ms = now_ms;
    parsed = ecobin_uart_stream_parser_feed(&endpoint->parser, data, length, now_ms, receive, endpoint);
    endpoint->feeding = 0u;
    return parsed;
}
