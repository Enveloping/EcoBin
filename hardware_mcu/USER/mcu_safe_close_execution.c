#include "mcu_safe_close_execution.h"
#include "actuator_runtime.h"
#include <string.h>

#define COMMAND(field) ECOBIN_UART_SAFE_CLOSE_##field##_OFFSET
#define EVENT(field) ECOBIN_UART_SAFE_CLOSE_RESULT_##field##_OFFSET
typedef char recovery_close_ram_budget[(sizeof(McuSafeCloseExecution) <= 80u) ? 1 : -1];

static void poll(McuControlEndpoint *endpoint, uint64_t now, void *context) {
    McuSafeCloseExecution *owner = (McuSafeCloseExecution *)context;
    ActuatorRecoveryClose close;
    uint8_t body[ECOBIN_UART_SAFE_CLOSE_RESULT_PAYLOAD_MAX_LENGTH];
    uint64_t at;
    uint8_t status;
    if (!owner->active || now < endpoint->last_input_ms) return;
    if (!owner->published) {
        close = ActuatorRuntime_RecoveryClose();
        if (owner->token) {
            if (!close.present || close.token != owner->token || !close.completed) return;
            at = close.terminal_at_ms;
            status = close.rejected ? ECOBIN_UART_DOOR_COMMAND_OUTPUT_STATUS_OUTPUT_REJECTED
                : close.coalesced ? ECOBIN_UART_DOOR_COMMAND_OUTPUT_STATUS_COALESCED_WITH_EXISTING_CLOSE
                : ECOBIN_UART_DOOR_COMMAND_OUTPUT_STATUS_COMMAND_DISPATCHED;
        } else {
            at = owner->rejected_at_ms;
            status = ECOBIN_UART_DOOR_COMMAND_OUTPUT_STATUS_OUTPUT_REJECTED;
        }
        if (now < at) return;
        memset(body, 0, sizeof(body));
        ecobin_uart_write_u64_be(body + EVENT(MCU_BOOT_ID), endpoint->session.boot_id);
        ecobin_uart_write_u64_be(body + EVENT(UPTIME_MS), at);
        memcpy(body + EVENT(MCU_COMMAND_UID), owner->command_uid, 16u);
        body[EVENT(SCOPE)] = ECOBIN_UART_SAFE_CLOSE_SCOPE_SINGLE_DELIVERY_DOOR;
        body[EVENT(PORT_NO)] = owner->port_no;
        body[EVENT(COMMAND)] = ECOBIN_UART_DELIVERY_DOOR_COMMAND_CLOSE;
        body[EVENT(OUTPUT_STATUS)] = status;
        body[EVENT(PHYSICAL_DOOR_STATE_BASIS)] = ECOBIN_UART_DOOR_PHYSICAL_STATE_BASIS_NOT_OBSERVABLE;
        ecobin_uart_write_u16_be(body + EVENT(FAULT_CODE), status == ECOBIN_UART_DOOR_COMMAND_OUTPUT_STATUS_OUTPUT_REJECTED
            ? ECOBIN_UART_FAULT_CODE_DELIVERY_DOOR_OUTPUT_REJECTED : ECOBIN_UART_FAULT_CODE_NONE);
        if (!McuControlEndpoint_PublishActuatorEvent(endpoint, &owner->record, 0u,
            ECOBIN_UART_MESSAGE_SAFE_CLOSE_RESULT, body, sizeof(body))) return;
        owner->published = 1u;
    }
    if (McuActuatorEventJournal_MemberSaved(&endpoint->actuator_events, &owner->record, 0u,
        ECOBIN_UART_MESSAGE_SAFE_CLOSE_RESULT)) {
        if (owner->token && !ActuatorRuntime_ReleaseRecoveryClose(owner->token)) return;
        owner->active = owner->preparation->recovery_active = 0u;
    }
}

static uint8_t receive(McuControlEndpoint *endpoint, uint8_t message, const uint8_t *payload,
    size_t length, uint64_t received, void *context, McuSessionDecision *decision) {
    McuSafeCloseExecution *owner = (McuSafeCloseExecution *)context;
    McuWorkPreparation *preparation = owner->preparation;
    McuSessionCommand command;
    McuActuatorEventReservation record = {0};
    ActuatorSnapshot snapshot;
    uint64_t deadline;
    uint16_t error;
    uint8_t coalesced;
    if (message != ECOBIN_UART_MESSAGE_SAFE_CLOSE) return 0u;
    command.target_boot_id = ecobin_uart_read_u64_be(payload + COMMAND(TARGET_MCU_BOOT_ID));
    command.sequence = ecobin_uart_read_u32_be(payload + COMMAND(COMMAND_SEQUENCE));
    memcpy(command.uid, payload + COMMAND(MCU_COMMAND_UID), 16u);
    memcpy(command.digest, payload + COMMAND(COMMAND_DIGEST_SHA256), 32u);
    if (!McuSession_QueryCommand(&endpoint->session, &command, decision)) return 0u;
    if (decision->outcome != ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN) return 1u;
    error = preparation->guard(message, payload, length, received, preparation->guard_context);
    if (error > ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT) error = ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT;
    snapshot = ActuatorRuntime_Snapshot();
    deadline = ecobin_uart_read_u32_be(payload + COMMAND(EXECUTION_DEADLINE_MS));
    coalesced = snapshot.door.target_valid && snapshot.door.action_active && snapshot.door.target == MCU_DIRECTION_CLOSE;
    if (error == ECOBIN_UART_NACK_ERROR_NONE) {
        if (payload[COMMAND(SCOPE)] != ECOBIN_UART_SAFE_CLOSE_SCOPE_SINGLE_DELIVERY_DOOR
            || payload[COMMAND(PORT_NO)] != endpoint->facts.port_no) error = ECOBIN_UART_NACK_ERROR_INVALID_FIELD;
        else if (owner->active || preparation->recovery_active || endpoint->work.status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
            || endpoint->work.result.held || endpoint->process_event.held || McuConfiguration_IsStaging(&preparation->configuration)
            || (preparation->weight.present && !preparation->weight.retired) || preparation->weight.in_flight
            || ActuatorRuntime_DeliveryCycle().present || ActuatorRuntime_CleanPulse().present || snapshot.lock_powered)
            error = ECOBIN_UART_NACK_ERROR_BUSY;
        else if (snapshot.update_latched) error = ECOBIN_UART_NACK_ERROR_SAFETY_BLOCKED;
        else if (received > UINT64_MAX - deadline || snapshot.captured_uptime_ms < received
            || snapshot.captured_uptime_ms > UINT64_MAX - 100u
            || received + deadline <= snapshot.captured_uptime_ms + (coalesced ? 0u : 100u)) error = ECOBIN_UART_NACK_ERROR_EXPIRED;
        else if (!McuControlEndpoint_ReserveActuatorEvents(endpoint, 1u, &record)) error = ECOBIN_UART_NACK_ERROR_BUSY;
    }
    if (!McuSession_ReceiveCommand(&endpoint->session, &command, error, decision) || !decision->execute_once) {
        if (record.boot_id) McuControlEndpoint_CancelActuatorEvents(endpoint, &record);
        return decision->outcome != ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN;
    }
    owner->record = record;
    memcpy(owner->command_uid, command.uid, 16u);
    owner->port_no = payload[COMMAND(PORT_NO)];
    owner->active = preparation->recovery_active = 1u;
    owner->published = 0u;
    owner->token = ActuatorRuntime_BeginRecoveryClose(received + deadline);
    owner->rejected_at_ms = ActuatorRuntime_Snapshot().captured_uptime_ms;
    return 1u;
}

uint8_t McuSafeCloseExecution_Attach(McuSafeCloseExecution *owner,
    McuWorkPreparation *preparation, McuControlEndpoint *endpoint) {
    if (owner == NULL || !McuWorkPreparation_AttachRecovery(preparation, endpoint, receive, poll, owner)) return 0u;
    memset(owner, 0, sizeof(*owner));
    owner->preparation = preparation;
    return 1u;
}
