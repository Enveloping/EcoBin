#include "mcu_clean_execution.h"
#include "actuator_runtime.h"
#include <string.h>

#define GRANT(field) ECOBIN_UART_UNLOCK_CLEAN_DOOR_##field##_OFFSET
#define START(field) ECOBIN_UART_START_CLEAN_OPERATION_##field##_OFFSET
#define EVENT(field) ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_##field##_OFFSET
#define INTENT(field) ECOBIN_UART_CLEAN_UNLOCK_REQUESTED_##field##_OFFSET
#define FINAL(field) ECOBIN_UART_CLEAN_FINAL_WEIGHT_READY_##field##_OFFSET
#define CONFIRM(field) ECOBIN_UART_CLEAN_COMPLETION_CONFIRMED_##field##_OFFSET
#define INTERRUPT(field) ECOBIN_UART_CLEAN_OPERATION_INTERRUPTED_##field##_OFFSET
#define SCOPE(field) (ECOBIN_UART_QUERY_PROCESS_EVENT_##field##_OFFSET - 8u)
#define DEVICE(field) (ECOBIN_UART_CONFIG_PREIMAGE_DEVICE_OFFSET + ECOBIN_UART_CONFIG_DEVICE_BLOCK_##field##_OFFSET - ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET)
typedef char clean_execution_ram_budget[(sizeof(McuCleanExecution) <= 384u) ? 1 : -1];
#define SAME_INTENT(field) typedef char clean_intent_##field##_matches[(INTENT(field) == ECOBIN_UART_CLEAN_FINISH_REQUESTED_##field##_OFFSET) ? 1 : -1]
SAME_INTENT(MCU_BOOT_ID);
SAME_INTENT(MCU_EVENT_SEQUENCE);
SAME_INTENT(UPTIME_MS);
SAME_INTENT(MCU_COMMAND_UID);
SAME_INTENT(OPERATION_UID);
SAME_INTENT(PORT_NO);
SAME_INTENT(CLEAN_ACTION_SEQUENCE);
SAME_INTENT(CONFIG_VERSION);
#undef SAME_INTENT

static void confirmation_payload(const McuCleanExecution *owner, const McuControlEndpoint *endpoint,
    uint32_t sequence, uint64_t at, uint8_t *body);

static uint8_t final_saved(const McuCleanExecution *owner, const McuControlEndpoint *endpoint) {
    const McuProcessEventSlot *slot = &endpoint->process_event;
    /* The immutable event was encoded and validated while MEASURING. Inspect
     * its saved identity here; never temporarily rewind the live work phase. */
    return (uint8_t)(owner->final_state == 2u && !slot->held
        && slot->boot_id == endpoint->session.boot_id
        && slot->length == ECOBIN_UART_CLEAN_FINAL_WEIGHT_READY_PAYLOAD_MAX_LENGTH
        && slot->highest_sequence != 0u && slot->highest_sequence == owner->final_measurement.event_sequence
        && memcmp(slot->scope, endpoint->work.identity, MCU_WORK_IDENTITY_LENGTH) == 0
        && slot->scope[SCOPE(EVENT_MESSAGE_TYPE)] == ECOBIN_UART_MESSAGE_CLEAN_FINAL_WEIGHT_READY
        && ecobin_uart_read_u16_be(slot->scope + SCOPE(STEP_SEQUENCE)) == owner->action_sequence
        && ecobin_uart_read_u64_be(slot->scope + SCOPE(CONFIG_VERSION)) == owner->final_meta.config_version
        && memcmp(slot->payload + FINAL(MEASUREMENT_UID), owner->final_measurement.uid, 16u) == 0
        && ecobin_uart_read_u64_be(slot->payload + FINAL(UPTIME_MS)) == owner->final_meta.observed_uptime_ms);
}

static uint8_t intent_saved(const McuCleanExecution *owner, const McuControlEndpoint *endpoint) {
    const McuProcessEventSlot *slot = &endpoint->process_event;
    const McuWorkPreparation *preparation = owner->preparation;
    uint8_t expected[ECOBIN_UART_CLEAN_UNLOCK_REQUESTED_PAYLOAD_MAX_LENGTH];
    if (slot->held || !owner->intent_event_sequence || slot->boot_id != endpoint->session.boot_id
        || slot->highest_sequence != owner->intent_event_sequence || slot->length != sizeof(expected)
        || memcmp(slot->scope, endpoint->work.identity, MCU_WORK_IDENTITY_LENGTH) != 0
        || slot->scope[SCOPE(EVENT_MESSAGE_TYPE)] != owner->intent_message
        || ecobin_uart_read_u16_be(slot->scope + SCOPE(STEP_SEQUENCE)) != owner->action_sequence
        || ecobin_uart_read_u64_be(slot->scope + SCOPE(CONFIG_VERSION)) != preparation->initial_meta.config_version) return 0u;
    memset(expected, 0, sizeof(expected));
    ecobin_uart_write_u64_be(expected + INTENT(MCU_BOOT_ID), endpoint->session.boot_id);
    ecobin_uart_write_u32_be(expected + INTENT(MCU_EVENT_SEQUENCE), owner->intent_event_sequence);
    ecobin_uart_write_u64_be(expected + INTENT(UPTIME_MS), owner->intent_at_ms);
    memcpy(expected + INTENT(MCU_COMMAND_UID), preparation->start_payload + START(MCU_COMMAND_UID), 16u);
    memcpy(expected + INTENT(OPERATION_UID), preparation->start_payload + START(OPERATION_UID), 16u);
    expected[INTENT(PORT_NO)] = preparation->start_payload[START(PORT_NO)];
    ecobin_uart_write_u16_be(expected + INTENT(CLEAN_ACTION_SEQUENCE), owner->action_sequence);
    ecobin_uart_write_u64_be(expected + INTENT(CONFIG_VERSION), preparation->initial_meta.config_version);
    return (uint8_t)(memcmp(expected, slot->payload, sizeof(expected)) == 0);
}

static uint16_t evaluate_reopen(const McuCleanExecution *owner, const McuControlEndpoint *endpoint,
    const uint8_t *payload, size_t length, uint64_t received, uint64_t now, McuOpeningLimits *limits) {
    const McuWorkPreparation *preparation = owner->preparation;
    const uint8_t *start = preparation->start_payload;
    uint32_t remaining = ecobin_uart_read_u32_be(payload + GRANT(REMAINING_OPERATION_WINDOW_MS));
    uint64_t deadline;
    uint16_t error;
    if (endpoint->application_context != preparation || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || preparation->start_message != ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION
        || memcmp(endpoint->work.identity, start, START(PORT_NO)) != 0
        || memcmp(payload + GRANT(OPERATION_UID), start + START(OPERATION_UID), 16u) != 0
        || memcmp(payload + GRANT(PARENT_COMMAND_UID), start + START(MCU_COMMAND_UID), 16u) != 0
        || payload[GRANT(PORT_NO)] != start[START(PORT_NO)] || payload[GRANT(PORT_NO)] != endpoint->facts.port_no)
        return ECOBIN_UART_NACK_ERROR_UNKNOWN_WORK;
    if (owner->interruption_reason) return owner->interruption_reason == ECOBIN_UART_CLEAN_INTERRUPTION_REASON_OPERATION_EXPIRED
        ? ECOBIN_UART_NACK_ERROR_EXPIRED : ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    if (endpoint->process_event.held || owner->active) return ECOBIN_UART_NACK_ERROR_BUSY;
    if (owner->intent_message != ECOBIN_UART_MESSAGE_CLEAN_UNLOCK_REQUESTED
        || endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_ACTIVE
        || ecobin_uart_read_u16_be(payload + GRANT(CLEAN_ACTION_SEQUENCE)) != owner->action_sequence
        || ecobin_uart_read_u32_be(payload + GRANT(RECOVERY_GENERATION)) != 0u
        || ecobin_uart_read_u64_be(owner->grant + GRANT(TARGET_MCU_BOOT_ID)) != endpoint->session.boot_id
        || !McuOpeningGate_ConfigurationMatches(preparation, endpoint) || !intent_saved(owner, endpoint)
        || !preparation->weight.retired || preparation->weight.in_flight
        || received < owner->intent_at_ms || now < received || now < endpoint->last_input_ms
        || now < preparation->weight.last_now_ms) return ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    if (!remaining || received > UINT64_MAX - remaining) return ECOBIN_UART_NACK_ERROR_EXPIRED;
    deadline = received + remaining;
    if (deadline > owner->operation_deadline_ms) deadline = owner->operation_deadline_ms;
    if (now >= deadline) return ECOBIN_UART_NACK_ERROR_EXPIRED;
    memset(limits, 0, sizeof(*limits));
    limits->execution_deadline_ms = limits->operation_deadline_ms = deadline;
    limits->unlock_pulse_ms = ecobin_uart_read_u32_be(preparation->configuration.active.preimage + DEVICE(CLEAN_SOLENOID_PULSE_MS));
    if (ecobin_uart_read_u32_be(payload + GRANT(UNLOCK_PULSE_MS)) != limits->unlock_pulse_ms)
        return ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    error = preparation->guard(ECOBIN_UART_MESSAGE_UNLOCK_CLEAN_DOOR, payload, length, now, preparation->guard_context);
    return error > ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT ? ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT : error;
}

static uint8_t publish(McuCleanExecution *owner, McuControlEndpoint *endpoint, uint8_t off, uint64_t at) {
    uint8_t body[ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_PAYLOAD_MAX_LENGTH];
    memset(body, 0, sizeof(body));
    ecobin_uart_write_u64_be(body + EVENT(MCU_BOOT_ID), endpoint->session.boot_id);
    ecobin_uart_write_u64_be(body + EVENT(UPTIME_MS), at);
    memcpy(body + EVENT(MCU_COMMAND_UID), owner->grant + GRANT(MCU_COMMAND_UID), 16u);
    memcpy(body + EVENT(OPERATION_UID), owner->grant + GRANT(OPERATION_UID), 16u);
    body[EVENT(PORT_NO)] = owner->grant[GRANT(PORT_NO)];
    body[EVENT(LOCK_POWER_STATE)] = off ? ECOBIN_UART_CLEAN_LOCK_POWER_STATE_DEENERGIZED : ECOBIN_UART_CLEAN_LOCK_POWER_STATE_ENERGIZED;
    body[EVENT(SOLENOID_HEALTH)] = ECOBIN_UART_SOLENOID_HEALTH_UNKNOWN;
    return McuControlEndpoint_PublishActuatorEvent(endpoint, &owner->records, off,
        ECOBIN_UART_MESSAGE_CLEAN_LOCK_POWER_CHANGED, body, sizeof(body)) != 0u;
}

static void poll_pulse(McuControlEndpoint *endpoint, uint64_t now, void *context) {
    McuCleanExecution *owner = (McuCleanExecution *)context;
    McuWorkPreparation *preparation = owner->preparation;
    ActuatorCleanPulse pulse;
    ActuatorSnapshot snapshot;
    if (!owner->active || endpoint->feeding || endpoint->application_context != preparation
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || preparation->start_message != ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION
        || ecobin_uart_read_u64_be(owner->grant + GRANT(TARGET_MCU_BOOT_ID)) != endpoint->session.boot_id
        || memcmp(endpoint->work.identity, preparation->start_payload, START(PORT_NO)) != 0
        || memcmp(owner->grant + GRANT(OPERATION_UID), preparation->start_payload + START(OPERATION_UID), 16u) != 0
        || now < endpoint->last_input_ms || now < preparation->weight.last_now_ms) return;
    pulse = ActuatorRuntime_CleanPulse();
    snapshot = ActuatorRuntime_Snapshot();
    if (!pulse.present || pulse.token != owner->pulse_token || now < pulse.powered_at_ms
        || now > snapshot.captured_uptime_ms) return;
    if (!owner->on_published) {
        if (!publish(owner, endpoint, 0u, pulse.powered_at_ms)) return;
        owner->on_published = 1u;
    }
    if (pulse.completed && now >= pulse.off_at_ms) {
        /* Stop may arrive after this loop's interruption check. Latch the
         * original pulse phase BEFORE retirement, or RECOVERY would hide it. */
        if (!owner->interruption_reason && (pulse.interrupted || snapshot.update_latched)) {
            owner->interruption_reason = snapshot.update_latched ? ECOBIN_UART_CLEAN_INTERRUPTION_REASON_UPDATE_STOPPED
                : ECOBIN_UART_CLEAN_INTERRUPTION_REASON_CONTROL_CONTEXT_LOST;
            owner->interrupted_phase = endpoint->work.phase;
            owner->interrupted_at_ms = now;
        }
        if (!owner->off_published) {
            if (!publish(owner, endpoint, 1u, pulse.off_at_ms)) return;
            owner->off_published = 1u;
        }
        if (!ActuatorRuntime_ReleaseCleanPulse(owner->pulse_token)) return;
        owner->active = 0u;
        McuWorkState_SetPhase(&endpoint->work, owner->interruption_reason || pulse.interrupted || snapshot.update_latched
            ? ECOBIN_UART_MCU_WORK_PHASE_CLEAN_RECOVERY_REQUIRED : ECOBIN_UART_MCU_WORK_PHASE_CLEAN_ACTIVE);
    }
}

static void poll_finish(McuCleanExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    McuWorkPreparation *preparation = owner->preparation;
    McuConfigWeightPolicy policy;
    ActuatorSnapshot snapshot = ActuatorRuntime_Snapshot();
    if (owner->interruption_reason || owner->active || owner->intent_message != ECOBIN_UART_MESSAGE_CLEAN_FINISH_REQUESTED || endpoint->feeding
        || endpoint->application_context != preparation || preparation->start_message != ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || memcmp(endpoint->work.identity, preparation->start_payload, START(PORT_NO)) != 0
        || ecobin_uart_read_u64_be(owner->grant + GRANT(TARGET_MCU_BOOT_ID)) != endpoint->session.boot_id
        || !McuOpeningGate_ConfigurationMatches(preparation, endpoint)
        || now < endpoint->last_input_ms || now < preparation->weight.last_now_ms || now < owner->intent_at_ms
        || now > snapshot.captured_uptime_ms || snapshot.update_latched || snapshot.lock_powered
        || !snapshot.door.target_valid || !snapshot.door.action_active || snapshot.door.target != MCU_DIRECTION_CLOSE) return;
    if (owner->final_state == 0u) {
        if (endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_ACTIVE || !intent_saved(owner, endpoint)) return;
        if (now >= owner->operation_deadline_ms || preparation->weight.measurement.result.measurement_id == UINT32_MAX
            || !McuConfiguration_ReadWeightPolicy(&preparation->configuration, preparation->start_payload[START(PORT_NO)], &policy)
            || !McuWeightRun_Begin(&preparation->weight, &policy, preparation->weight.measurement.result.measurement_id + 1u, now)) {
            McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_RECOVERY_REQUIRED);
            return;
        }
        memset(&owner->final_measurement, 0, sizeof(owner->final_measurement));
        memset(&owner->final_meta, 0, sizeof(owner->final_meta));
        owner->final_state = 1u;
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_FINAL_MEASURING);
    }
    if (owner->final_state == 1u && McuWorkPreparation_PollMeasurement(preparation, endpoint, now,
        ECOBIN_UART_MESSAGE_CLEAN_FINAL_WEIGHT_READY, owner->action_sequence,
        &owner->final_measurement, &owner->final_meta) == 2u) {
        owner->final_state = 2u;
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_RESULT_CONFIRMATION);
    }
}

static void poll_initial_failure(McuCleanExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    McuWorkPreparation *preparation = owner->preparation;
    const uint8_t *start = preparation->start_payload;
    McuResultSummary summary;
    McuResultMeasurement not_taken;
    if (endpoint->feeding || endpoint->application_context != preparation || owner->active
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_FINALIZING
        || preparation->start_message != ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION
        || !preparation->initial_ready || !preparation->weight.retired || preparation->weight.in_flight
        || (preparation->initial.kind != ECOBIN_UART_RESULT_MEASUREMENT_KIND_UNAVAILABLE
            && preparation->initial.kind != ECOBIN_UART_RESULT_MEASUREMENT_KIND_INTERRUPTED)
        || preparation->initial.source_boot_id != endpoint->session.boot_id
        || preparation->initial_meta.config_version != ecobin_uart_read_u64_be(start + START(CONFIG_VERSION))
        || memcmp(endpoint->work.identity, start, START(PORT_NO)) != 0
        || endpoint->work.identity[MCU_WORK_IDENTITY_LENGTH - 2u] != ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION
        || endpoint->work.identity[MCU_WORK_IDENTITY_LENGTH - 1u] != start[START(PORT_NO)]
        || now < endpoint->last_input_ms || now < preparation->weight.last_now_ms
        || now < preparation->initial_meta.observed_uptime_ms || now > ActuatorRuntime_Snapshot().captured_uptime_ms
        || !McuOpeningGate_InitialSaved(preparation, endpoint, 1u)) return;
    /* The first unlock was never authorized. Preserve the original failed
     * attempt; no final measurement, cleaner confirmation, lock/door action or
     * old execution-owner fields can be invented for this new work. */
    memset(&summary, 0, sizeof(summary));
    memset(&not_taken, 0, sizeof(not_taken));
    summary.config_version = preparation->initial_meta.config_version;
    summary.completed_uptime_ms = preparation->initial_meta.observed_uptime_ms;
    summary.finish_reason = ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED;
    McuResultBuilder_Complete(&endpoint->work, &summary, &preparation->initial, &not_taken,
        preparation->scratch, sizeof(preparation->scratch));
}

static void poll_confirmed(McuCleanExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    McuWorkPreparation *preparation = owner->preparation;
    const McuProcessEventSlot *slot = &endpoint->process_event;
    McuResultSummary summary;
    if (endpoint->feeding || endpoint->application_context != preparation || owner->active
        || !owner->confirmation_event_sequence || owner->final_state != 2u
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_FINALIZING
        || preparation->start_message != ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION
        || memcmp(endpoint->work.identity, preparation->start_payload, START(PORT_NO)) != 0
        || ecobin_uart_read_u64_be(owner->grant + GRANT(TARGET_MCU_BOOT_ID)) != endpoint->session.boot_id
        || now < endpoint->last_input_ms || now < owner->confirmation_at_ms
        || slot->held || slot->boot_id != endpoint->session.boot_id
        || slot->highest_sequence != owner->confirmation_event_sequence
        || slot->length != ECOBIN_UART_CLEAN_COMPLETION_CONFIRMED_PAYLOAD_MAX_LENGTH
        || memcmp(slot->scope, endpoint->work.identity, MCU_WORK_IDENTITY_LENGTH) != 0
        || slot->scope[SCOPE(EVENT_MESSAGE_TYPE)] != ECOBIN_UART_MESSAGE_CLEAN_COMPLETION_CONFIRMED
        || ecobin_uart_read_u16_be(slot->scope + SCOPE(STEP_SEQUENCE)) != owner->action_sequence
        || ecobin_uart_read_u64_be(slot->scope + SCOPE(CONFIG_VERSION)) != preparation->initial_meta.config_version) return;
    confirmation_payload(owner, endpoint, owner->confirmation_event_sequence, owner->confirmation_at_ms, preparation->scratch);
    if (memcmp(slot->payload, preparation->scratch, slot->length) != 0) return;
    /* Once human confirmation is frozen, later clock/power/update observations
     * cannot rewrite the earlier fact. Only exact custody precedes assembly. */
    memset(&summary, 0, sizeof(summary));
    summary.config_version = preparation->initial_meta.config_version;
    summary.completed_uptime_ms = owner->confirmation_at_ms;
    summary.clean_action_sequence = owner->action_sequence;
    summary.finish_reason = owner->final_measurement.kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_INTERRUPTED
        ? ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED : ECOBIN_UART_WORK_RESULT_FINISH_REASON_CLEAN_CONFIRMED;
    summary.physical_close_confirmed = 1u;
    if (McuResultBuilder_Complete(&endpoint->work, &summary, &preparation->initial,
        &owner->final_measurement, preparation->scratch, sizeof(preparation->scratch))) {
        /* No interruption can rewrite the frozen human fact. Return only the
         * unused independent reservation; never cancel actual ON/OFF evidence. */
        McuControlEndpoint_CancelActuatorEvents(endpoint, &owner->interruption_record);
        memset(&owner->interruption_record, 0, sizeof(owner->interruption_record));
    }
}

static void poll_interruption(McuCleanExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    McuWorkPreparation *preparation = owner->preparation;
    ActuatorSnapshot snapshot = ActuatorRuntime_Snapshot();
    ActuatorCleanPulse pulse = ActuatorRuntime_CleanPulse();
    uint8_t body[ECOBIN_UART_CLEAN_OPERATION_INTERRUPTED_PAYLOAD_MAX_LENGTH];
    if (endpoint->feeding || endpoint->application_context != preparation
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || preparation->start_message != ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION
        || !owner->interruption_record.boot_id || owner->interruption_published
        || ecobin_uart_read_u64_be(owner->grant + GRANT(TARGET_MCU_BOOT_ID)) != endpoint->session.boot_id
        || memcmp(endpoint->work.identity, preparation->start_payload, START(PORT_NO)) != 0
        || memcmp(owner->grant + GRANT(OPERATION_UID), preparation->start_payload + START(OPERATION_UID), 16u) != 0
        || now < endpoint->last_input_ms || now < preparation->weight.last_now_ms
        || now > snapshot.captured_uptime_ms) return;
    if (!owner->interruption_reason) {
        if (endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_ACTIVE
            && endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_UNLOCK_PULSE
            && endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_FINAL_MEASURING
            && endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_RESULT_CONFIRMATION) return;
        if (snapshot.update_latched) owner->interruption_reason = ECOBIN_UART_CLEAN_INTERRUPTION_REASON_UPDATE_STOPPED;
        else if (!snapshot.door.target_valid || !snapshot.door.action_active || snapshot.door.target != MCU_DIRECTION_CLOSE
            || (owner->active && (!pulse.present || pulse.token != owner->pulse_token))
            || (!owner->active && (pulse.present || snapshot.lock_powered)))
            owner->interruption_reason = ECOBIN_UART_CLEAN_INTERRUPTION_REASON_CONTROL_CONTEXT_LOST;
        else if (now >= owner->operation_deadline_ms) owner->interruption_reason = ECOBIN_UART_CLEAN_INTERRUPTION_REASON_OPERATION_EXPIRED;
        else return;
        owner->interrupted_phase = endpoint->work.phase;
        owner->interrupted_at_ms = now;
    }
    /* Normal duration is timer-owned; a foreground expiry/control fault stops
     * only our exact retained pulse. Never fake an update or stop another token.
     * Missing runtime evidence remains missing, not an invented power edge. */
    if (owner->active && pulse.present && pulse.token == owner->pulse_token)
        ActuatorRuntime_AbortCleanPulse(owner->pulse_token);
    poll_pulse(endpoint, now, owner);
    if (owner->active && pulse.present && pulse.token == owner->pulse_token) return;
    if (owner->interrupted_phase == ECOBIN_UART_MCU_WORK_PHASE_CLEAN_FINAL_MEASURING && owner->final_state == 1u) {
        if (!McuWorkPreparation_InterruptMeasurement(preparation, now)
            || McuWorkPreparation_PollMeasurement(preparation, endpoint, now,
                ECOBIN_UART_MESSAGE_CLEAN_FINAL_WEIGHT_READY, owner->action_sequence,
                &owner->final_measurement, &owner->final_meta) != 2u) return;
        owner->final_state = 2u;
    }
    memset(body, 0, sizeof(body));
    ecobin_uart_write_u64_be(body + INTERRUPT(MCU_BOOT_ID), endpoint->session.boot_id);
    ecobin_uart_write_u64_be(body + INTERRUPT(UPTIME_MS), owner->interrupted_at_ms);
    memcpy(body + INTERRUPT(MCU_COMMAND_UID), owner->grant + GRANT(MCU_COMMAND_UID), 16u);
    memcpy(body + INTERRUPT(OPERATION_UID), owner->grant + GRANT(OPERATION_UID), 16u);
    body[INTERRUPT(PORT_NO)] = owner->grant[GRANT(PORT_NO)];
    ecobin_uart_write_u16_be(body + INTERRUPT(CLEAN_ACTION_SEQUENCE), owner->action_sequence);
    body[INTERRUPT(INTERRUPTED_PHASE)] = owner->interrupted_phase;
    body[INTERRUPT(INTERRUPTION_REASON)] = owner->interruption_reason;
    if (owner->interrupted_phase == ECOBIN_UART_MCU_WORK_PHASE_CLEAN_FINAL_MEASURING
        || owner->interrupted_phase == ECOBIN_UART_MCU_WORK_PHASE_CLEAN_RESULT_CONFIRMATION)
        ecobin_uart_write_u32_be(body + INTERRUPT(FINAL_MEASUREMENT_EVENT_SEQUENCE), owner->final_measurement.event_sequence);
    owner->interruption_published = (uint8_t)(McuControlEndpoint_PublishActuatorEvent(endpoint,
        &owner->interruption_record, 0u, ECOBIN_UART_MESSAGE_CLEAN_OPERATION_INTERRUPTED, body, sizeof(body)) != 0u);
    McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_RECOVERY_REQUIRED);
}

static void poll(McuControlEndpoint *endpoint, uint64_t now, void *context) {
    poll_initial_failure((McuCleanExecution *)context, endpoint, now);
    poll_interruption((McuCleanExecution *)context, endpoint, now);
    poll_pulse(endpoint, now, context);
    poll_finish((McuCleanExecution *)context, endpoint, now);
    poll_confirmed((McuCleanExecution *)context, endpoint, now);
}

static uint8_t receive(McuControlEndpoint *endpoint, uint8_t message, const uint8_t *payload,
    size_t length, uint64_t received, void *context, McuSessionDecision *decision) {
    McuCleanExecution *owner = (McuCleanExecution *)context;
    McuSessionCommand command;
    McuOpeningLimits limits;
    McuActuatorEventReservation records = {0};
    McuActuatorEventReservation interruption = {0};
    ActuatorSnapshot snapshot;
    uint16_t error;
    if (message != ECOBIN_UART_MESSAGE_UNLOCK_CLEAN_DOOR) return 0u;
    command.target_boot_id = ecobin_uart_read_u64_be(payload + GRANT(TARGET_MCU_BOOT_ID));
    command.sequence = ecobin_uart_read_u32_be(payload + GRANT(COMMAND_SEQUENCE));
    memcpy(command.uid, payload + GRANT(MCU_COMMAND_UID), sizeof(command.uid));
    memcpy(command.digest, payload + GRANT(COMMAND_DIGEST_SHA256), sizeof(command.digest));
    if (!McuSession_QueryCommand(&endpoint->session, &command, decision)) return 0u;
    if (decision->outcome != ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN) return 1u;
    snapshot = ActuatorRuntime_Snapshot();
    error = ecobin_uart_read_u16_be(payload + GRANT(CLEAN_ACTION_SEQUENCE)) == 0u
        ? McuOpeningGate_Evaluate(owner->preparation, endpoint, message, payload, length,
            received, snapshot.captured_uptime_ms, &limits)
        : evaluate_reopen(owner, endpoint, payload, length, received, snapshot.captured_uptime_ms, &limits);
    if (error == ECOBIN_UART_NACK_ERROR_NONE) {
        /* Re-read after the mandatory external guard; it cannot grant an old
         * snapshot authority over a later update or expired clock. */
        snapshot = ActuatorRuntime_Snapshot();
        if (owner->active || ActuatorRuntime_CleanPulse().present || ActuatorRuntime_DeliveryCycle().present
            || snapshot.lock_powered) error = ECOBIN_UART_NACK_ERROR_BUSY;
        else if (snapshot.update_latched || !snapshot.door.target_valid || !snapshot.door.action_active
            || snapshot.door.target != MCU_DIRECTION_CLOSE) error = ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
        else if (snapshot.captured_uptime_ms >= limits.execution_deadline_ms
            || snapshot.captured_uptime_ms > UINT64_MAX - limits.unlock_pulse_ms) error = ECOBIN_UART_NACK_ERROR_EXPIRED;
        else if (!McuControlEndpoint_ReserveActuatorEvents(endpoint, 2u, &records)) error = ECOBIN_UART_NACK_ERROR_BUSY;
        else if (ecobin_uart_read_u16_be(payload + GRANT(CLEAN_ACTION_SEQUENCE)) == 0u
            && !McuControlEndpoint_ReserveActuatorEvents(endpoint, 1u, &interruption)) error = ECOBIN_UART_NACK_ERROR_BUSY;
    }
    if (!McuSession_ReceiveCommand(&endpoint->session, &command, error, decision)) {
        if (records.boot_id) McuControlEndpoint_CancelActuatorEvents(endpoint, &records);
        if (interruption.boot_id) McuControlEndpoint_CancelActuatorEvents(endpoint, &interruption);
        return 0u;
    }
    if (error != ECOBIN_UART_NACK_ERROR_NONE || !decision->execute_once) {
        if (records.boot_id) McuControlEndpoint_CancelActuatorEvents(endpoint, &records);
        if (interruption.boot_id) McuControlEndpoint_CancelActuatorEvents(endpoint, &interruption);
        return 1u;
    }
    memcpy(owner->grant, payload, length);
    owner->records = records;
    owner->operation_deadline_ms = limits.operation_deadline_ms;
    if (ecobin_uart_read_u16_be(payload + GRANT(CLEAN_ACTION_SEQUENCE)) == 0u) {
        owner->interruption_record = interruption;
        owner->interruption_reason = owner->interrupted_phase = owner->interruption_published = 0u;
        owner->interrupted_at_ms = 0u;
        owner->intent_event_sequence = 0u;
        owner->intent_at_ms = 0u;
        owner->confirmation_event_sequence = 0u;
        owner->confirmation_at_ms = 0u;
        owner->action_sequence = 0u;
        owner->final_state = 0u;
        memset(&owner->final_measurement, 0, sizeof(owner->final_measurement));
        memset(&owner->final_meta, 0, sizeof(owner->final_meta));
    }
    owner->intent_message = 0u;
    owner->on_published = owner->off_published = 0u;
    owner->pulse_token = ActuatorRuntime_BeginCleanPulse(limits.execution_deadline_ms, limits.unlock_pulse_ms);
    owner->active = owner->pulse_token != 0u;
    if (!owner->active) {
        /* Acceptance is immutable even if the final timer-side check rejects.
         * No power edge occurred: do not fabricate ENERGIZED/DEENERGIZED. */
        McuControlEndpoint_CancelActuatorEvents(endpoint, &owner->records);
        owner->interruption_reason = ECOBIN_UART_CLEAN_INTERRUPTION_REASON_UNLOCK_DISPATCH_REJECTED;
        owner->interrupted_phase = endpoint->work.phase;
        owner->interrupted_at_ms = ActuatorRuntime_Snapshot().captured_uptime_ms;
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_RECOVERY_REQUIRED);
    } else McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_UNLOCK_PULSE);
    return 1u;
}

uint8_t McuCleanExecution_Request(McuCleanExecution *owner, McuControlEndpoint *endpoint,
    const uint8_t *operation_uid, uint8_t message, uint16_t after_action_sequence, uint64_t now) {
    McuWorkPreparation *preparation;
    ActuatorSnapshot snapshot;
    uint8_t body[ECOBIN_UART_CLEAN_UNLOCK_REQUESTED_PAYLOAD_MAX_LENGTH];
    uint8_t scope[ECOBIN_UART_QUERY_PROCESS_EVENT_PAYLOAD_MAX_LENGTH - 8u];
    uint8_t invalidates_final;
    uint32_t sequence;
    if (owner == NULL || endpoint == NULL || operation_uid == NULL || owner->preparation == NULL
        || (message != ECOBIN_UART_MESSAGE_CLEAN_UNLOCK_REQUESTED && message != ECOBIN_UART_MESSAGE_CLEAN_FINISH_REQUESTED)) return 0u;
    preparation = owner->preparation;
    snapshot = ActuatorRuntime_Snapshot();
    invalidates_final = (uint8_t)(message == ECOBIN_UART_MESSAGE_CLEAN_UNLOCK_REQUESTED
        && endpoint->work.phase == ECOBIN_UART_MCU_WORK_PHASE_CLEAN_RESULT_CONFIRMATION
        && owner->intent_message == ECOBIN_UART_MESSAGE_CLEAN_FINISH_REQUESTED && final_saved(owner, endpoint));
    if (endpoint->feeding || endpoint->application_context != preparation || owner->interruption_reason || owner->active || !owner->off_published
        || (!invalidates_final && (owner->intent_message || endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_ACTIVE))
        || owner->action_sequence != after_action_sequence || owner->action_sequence == UINT16_MAX
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || preparation->start_message != ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION
        || memcmp(operation_uid, preparation->start_payload + START(OPERATION_UID), 16u) != 0
        || memcmp(owner->grant + GRANT(OPERATION_UID), operation_uid, 16u) != 0
        || memcmp(endpoint->work.identity, preparation->start_payload, START(PORT_NO)) != 0
        || ecobin_uart_read_u64_be(owner->grant + GRANT(TARGET_MCU_BOOT_ID)) != endpoint->session.boot_id
        || !McuOpeningGate_ConfigurationMatches(preparation, endpoint)
        || endpoint->process_event.held || !preparation->weight.retired || preparation->weight.in_flight
        || !McuActuatorEventJournal_MemberSaved(&endpoint->actuator_events, &owner->records, 0u, ECOBIN_UART_MESSAGE_CLEAN_LOCK_POWER_CHANGED)
        || !McuActuatorEventJournal_MemberSaved(&endpoint->actuator_events, &owner->records, 1u, ECOBIN_UART_MESSAGE_CLEAN_LOCK_POWER_CHANGED)
        || snapshot.update_latched || snapshot.lock_powered || !snapshot.door.target_valid || !snapshot.door.action_active
        || snapshot.door.target != MCU_DIRECTION_CLOSE || now < endpoint->last_input_ms || now < preparation->weight.last_now_ms
        || now < owner->intent_at_ms || now >= owner->operation_deadline_ms || now > snapshot.captured_uptime_ms) return 0u;
    memset(body, 0, sizeof(body));
    ecobin_uart_write_u64_be(body + INTENT(MCU_BOOT_ID), endpoint->session.boot_id);
    ecobin_uart_write_u64_be(body + INTENT(UPTIME_MS), now);
    memcpy(body + INTENT(MCU_COMMAND_UID), preparation->start_payload + START(MCU_COMMAND_UID), 16u);
    memcpy(body + INTENT(OPERATION_UID), operation_uid, 16u);
    body[INTENT(PORT_NO)] = preparation->start_payload[START(PORT_NO)];
    ecobin_uart_write_u16_be(body + INTENT(CLEAN_ACTION_SEQUENCE), (uint16_t)(owner->action_sequence + 1u));
    ecobin_uart_write_u64_be(body + INTENT(CONFIG_VERSION), preparation->initial_meta.config_version);
    sequence = McuControlEndpoint_ReserveEventSequence(endpoint);
    if (!sequence) return 0u;
    ecobin_uart_write_u32_be(body + INTENT(MCU_EVENT_SEQUENCE), sequence);
    /* A denied intent may leave a sequence gap, never a forged zero identity. */
    if (preparation->guard(message, body, sizeof(body), now, preparation->guard_context) != ECOBIN_UART_NACK_ERROR_NONE) return 0u;
    snapshot = ActuatorRuntime_Snapshot();
    if (snapshot.update_latched || snapshot.lock_powered || !snapshot.door.target_valid || !snapshot.door.action_active
        || snapshot.door.target != MCU_DIRECTION_CLOSE || snapshot.captured_uptime_ms < now
        || snapshot.captured_uptime_ms >= owner->operation_deadline_ms) return 0u;
    memcpy(scope, endpoint->work.identity, MCU_WORK_IDENTITY_LENGTH);
    scope[SCOPE(EVENT_MESSAGE_TYPE)] = message;
    ecobin_uart_write_u16_be(scope + SCOPE(STEP_SEQUENCE), (uint16_t)(owner->action_sequence + 1u));
    ecobin_uart_write_u64_be(scope + SCOPE(CONFIG_VERSION), preparation->initial_meta.config_version);
    if (!McuProcessEventSlot_Freeze(&endpoint->process_event, scope, sizeof(scope), message, body, sizeof(body))) return 0u;
    owner->intent_event_sequence = sequence;
    owner->intent_at_ms = now;
    ++owner->action_sequence;
    owner->intent_message = message;
    if (invalidates_final) {
        /* Old raw weight remains in Pi custody, but is no longer a completion
         * candidate. The next FINISH must start a distinct measurement. */
        owner->final_state = 0u;
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_ACTIVE);
    }
    return 1u;
}

static void confirmation_payload(const McuCleanExecution *owner, const McuControlEndpoint *endpoint,
    uint32_t sequence, uint64_t at, uint8_t *body) {
    const McuWorkPreparation *preparation = owner->preparation;
    memset(body, 0, ECOBIN_UART_CLEAN_COMPLETION_CONFIRMED_PAYLOAD_MAX_LENGTH);
    ecobin_uart_write_u64_be(body + CONFIRM(MCU_BOOT_ID), endpoint->session.boot_id);
    ecobin_uart_write_u32_be(body + CONFIRM(MCU_EVENT_SEQUENCE), sequence);
    ecobin_uart_write_u64_be(body + CONFIRM(UPTIME_MS), at);
    memcpy(body + CONFIRM(MCU_COMMAND_UID), preparation->start_payload + START(MCU_COMMAND_UID), 16u);
    memcpy(body + CONFIRM(OPERATION_UID), preparation->start_payload + START(OPERATION_UID), 16u);
    body[CONFIRM(PORT_NO)] = preparation->start_payload[START(PORT_NO)];
    ecobin_uart_write_u16_be(body + CONFIRM(CLEAN_ACTION_SEQUENCE), owner->action_sequence);
    memcpy(body + CONFIRM(FINAL_MEASUREMENT_UID), owner->final_measurement.uid, 16u);
    body[CONFIRM(LOCK_POWER_STATE)] = ECOBIN_UART_CLEAN_LOCK_POWER_STATE_DEENERGIZED;
    body[CONFIRM(SOLENOID_HEALTH)] = ECOBIN_UART_SOLENOID_HEALTH_UNKNOWN;
    body[CONFIRM(CLEAN_DOOR_STATE_BASIS)] = ECOBIN_UART_CLEAN_DOOR_STATE_BASIS_CLEANER_CONFIRMATION;
    body[CONFIRM(CLEANER_PHYSICAL_CLOSE_CONFIRMED)] = 1u;
    ecobin_uart_write_u64_be(body + CONFIRM(CONFIG_VERSION), preparation->initial_meta.config_version);
}

uint8_t McuCleanExecution_Confirm(McuCleanExecution *owner, McuControlEndpoint *endpoint,
    const uint8_t *operation_uid, uint16_t action_sequence, const uint8_t *final_measurement_uid, uint64_t now) {
    McuWorkPreparation *preparation;
    ActuatorSnapshot snapshot;
    uint8_t body[ECOBIN_UART_CLEAN_COMPLETION_CONFIRMED_PAYLOAD_MAX_LENGTH];
    uint8_t scope[ECOBIN_UART_QUERY_PROCESS_EVENT_PAYLOAD_MAX_LENGTH - 8u];
    uint32_t sequence;
    if (owner == NULL || endpoint == NULL || operation_uid == NULL || final_measurement_uid == NULL
        || owner->preparation == NULL) return 0u;
    preparation = owner->preparation;
    snapshot = ActuatorRuntime_Snapshot();
    if (endpoint->feeding || endpoint->application_context != preparation || owner->interruption_reason || owner->active || !owner->off_published
        || owner->confirmation_event_sequence || owner->intent_message != ECOBIN_UART_MESSAGE_CLEAN_FINISH_REQUESTED
        || owner->action_sequence != action_sequence || action_sequence == 0u
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_RESULT_CONFIRMATION
        || preparation->start_message != ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION
        || memcmp(operation_uid, preparation->start_payload + START(OPERATION_UID), 16u) != 0
        || memcmp(owner->grant + GRANT(OPERATION_UID), operation_uid, 16u) != 0
        || memcmp(endpoint->work.identity, preparation->start_payload, START(PORT_NO)) != 0
        || ecobin_uart_read_u64_be(owner->grant + GRANT(TARGET_MCU_BOOT_ID)) != endpoint->session.boot_id
        || !McuOpeningGate_ConfigurationMatches(preparation, endpoint) || !final_saved(owner, endpoint)
        || memcmp(final_measurement_uid, owner->final_measurement.uid, 16u) != 0
        || !preparation->weight.retired || preparation->weight.in_flight
        || !McuActuatorEventJournal_MemberSaved(&endpoint->actuator_events, &owner->records, 0u, ECOBIN_UART_MESSAGE_CLEAN_LOCK_POWER_CHANGED)
        || !McuActuatorEventJournal_MemberSaved(&endpoint->actuator_events, &owner->records, 1u, ECOBIN_UART_MESSAGE_CLEAN_LOCK_POWER_CHANGED)
        || snapshot.update_latched || snapshot.lock_powered || !snapshot.door.target_valid || !snapshot.door.action_active
        || snapshot.door.target != MCU_DIRECTION_CLOSE || now < endpoint->last_input_ms || now < preparation->weight.last_now_ms
        || now < owner->final_meta.observed_uptime_ms || now >= owner->operation_deadline_ms || now > snapshot.captured_uptime_ms) return 0u;
    sequence = McuControlEndpoint_ReserveEventSequence(endpoint);
    if (!sequence) return 0u;
    confirmation_payload(owner, endpoint, sequence, now, body);
    if (preparation->guard(ECOBIN_UART_MESSAGE_CLEAN_COMPLETION_CONFIRMED, body, sizeof(body), now,
        preparation->guard_context) != ECOBIN_UART_NACK_ERROR_NONE) return 0u;
    snapshot = ActuatorRuntime_Snapshot();
    if (snapshot.update_latched || snapshot.lock_powered || !snapshot.door.target_valid || !snapshot.door.action_active
        || snapshot.door.target != MCU_DIRECTION_CLOSE || snapshot.captured_uptime_ms < now
        || snapshot.captured_uptime_ms >= owner->operation_deadline_ms) return 0u;
    memcpy(scope, endpoint->work.identity, MCU_WORK_IDENTITY_LENGTH);
    scope[SCOPE(EVENT_MESSAGE_TYPE)] = ECOBIN_UART_MESSAGE_CLEAN_COMPLETION_CONFIRMED;
    ecobin_uart_write_u16_be(scope + SCOPE(STEP_SEQUENCE), action_sequence);
    ecobin_uart_write_u64_be(scope + SCOPE(CONFIG_VERSION), preparation->initial_meta.config_version);
    if (!McuProcessEventSlot_Freeze(&endpoint->process_event, scope, sizeof(scope),
        ECOBIN_UART_MESSAGE_CLEAN_COMPLETION_CONFIRMED, body, sizeof(body))) return 0u;
    owner->confirmation_event_sequence = sequence;
    owner->confirmation_at_ms = now;
    McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_FINALIZING);
    return 1u;
}

uint8_t McuCleanExecution_Attach(McuCleanExecution *owner,
    McuWorkPreparation *preparation, McuControlEndpoint *endpoint) {
    if (owner == NULL || !McuWorkPreparation_AttachActions(preparation, endpoint,
        ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION, receive, poll, owner)) return 0u;
    memset(owner, 0, sizeof(*owner));
    owner->preparation = preparation;
    return 1u;
}
