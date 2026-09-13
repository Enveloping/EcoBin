#include "mcu_clean_execution.h"
#include "actuator_runtime.h"
#include <string.h>
#define START(field) ECOBIN_UART_START_CLEAN_OPERATION_##field##_OFFSET
typedef char clean_execution_ram_budget[(sizeof(McuCleanExecution) <= 192u) ? 1 : -1];

static uint8_t available(const McuResultMeasurement *measurement) {
    return measurement->kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_STABLE_MEAN
        || measurement->kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_TIMEOUT_MEDIAN;
}
static uint8_t context_valid(const McuCleanExecution *owner, const McuControlEndpoint *endpoint, uint64_t now) {
    const McuWorkPreparation *preparation = owner->preparation;
    return (uint8_t)(!endpoint->feeding && endpoint->application_context == preparation
        && preparation->start_message == ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION
        && endpoint->work.status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        && memcmp(endpoint->work.identity, preparation->start_payload, START(PORT_NO)) == 0
        && now >= endpoint->last_input_ms && now >= owner->last_now_ms
        && now <= UINT64_C(9007199254740991) && now <= ActuatorRuntime_Snapshot().captured_uptime_ms);
}
static void complete(McuCleanExecution *owner, McuControlEndpoint *endpoint, uint8_t reason, uint64_t now) {
    McuResultSummary summary = {0};
    McuResultMeasurement not_taken = {0};
    ActuatorSnapshot snapshot = ActuatorRuntime_Snapshot();
    if (reason == ECOBIN_UART_WORK_RESULT_FINISH_REASON_CLEAN_CONFIRMED
        && (snapshot.update_latched || snapshot.lock_powered || !snapshot.door.target_valid
            || !snapshot.door.action_active || snapshot.door.target != MCU_DIRECTION_CLOSE))
        reason = ECOBIN_UART_WORK_RESULT_FINISH_REASON_CANCELLED;
    summary.config_version = owner->preparation->initial_meta.config_version;
    summary.completed_uptime_ms = now;
    summary.clean_action_sequence = owner->action_sequence;
    summary.finish_reason = reason;
    summary.physical_close_confirmed = owner->physical_close_confirmed;
    McuResultBuilder_Complete(&endpoint->work, &summary, &owner->preparation->initial,
        owner->final_state == 2u ? &owner->final_measurement : &not_taken,
        owner->preparation->scratch, sizeof(owner->preparation->scratch));
}
static uint8_t begin_pulse(McuCleanExecution *owner, McuControlEndpoint *endpoint, uint64_t deadline) {
    owner->pulse_token = ActuatorRuntime_BeginCleanPulse(deadline, owner->unlock_pulse_ms);
    if (!owner->pulse_token) return 0u;
    owner->active = 1u;
    McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_UNLOCK_PULSE);
    return 1u;
}
static void poll(McuControlEndpoint *endpoint, uint64_t now, void *context) {
    McuCleanExecution *owner = (McuCleanExecution *)context;
    McuWorkPreparation *preparation = owner->preparation;
    McuOpeningLimits limits;
    ActuatorCleanPulse pulse;
    ActuatorSnapshot snapshot;
    uint8_t interrupted;
    if (!context_valid(owner, endpoint, now)) return;
    owner->last_now_ms = now;
    if (!preparation->initial_ready) return;
    if (memcmp(owner->start_uid, preparation->start_payload + START(MCU_COMMAND_UID), 16u) != 0) {
        memset(owner, 0, sizeof(*owner));
        owner->preparation = preparation;
        owner->last_now_ms = now;
        memcpy(owner->start_uid, preparation->start_payload + START(MCU_COMMAND_UID), 16u);
        if (!available(&preparation->initial)
            || McuOpeningGate_StartLimits(preparation, endpoint, now, &limits) != ECOBIN_UART_NACK_ERROR_NONE) {
            complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED, now);
            return;
        }
        owner->operation_deadline_ms = limits.operation_deadline_ms;
        owner->unlock_pulse_ms = limits.unlock_pulse_ms;
        if (!begin_pulse(owner, endpoint, limits.execution_deadline_ms))
            complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_CANCELLED, now);
        else owner->started = 1u;
        return;
    }
    snapshot = ActuatorRuntime_Snapshot();
    pulse = ActuatorRuntime_CleanPulse();
    interrupted = snapshot.update_latched || !snapshot.door.target_valid || !snapshot.door.action_active
        || snapshot.door.target != MCU_DIRECTION_CLOSE || now >= owner->operation_deadline_ms
        || (owner->active && (!pulse.present || pulse.token != owner->pulse_token || pulse.interrupted));
    if (interrupted) {
        if (owner->active && pulse.present && pulse.token == owner->pulse_token) {
            ActuatorRuntime_AbortCleanPulse(owner->pulse_token);
            ActuatorRuntime_ReleaseCleanPulse(owner->pulse_token);
        }
        owner->active = 0u;
        if (owner->final_state == 1u) {
            if (!McuWorkPreparation_InterruptMeasurement(preparation, now)
                || McuWorkPreparation_PollMeasurement(preparation, endpoint, now,
                    ECOBIN_UART_MESSAGE_CLEAN_FINAL_WEIGHT_READY, owner->action_sequence,
                    &owner->final_measurement, &owner->final_meta) != 2u) return;
            owner->final_state = 2u;
        }
        complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_CANCELLED, now);
        return;
    }
    if (owner->active) {
        if (!pulse.completed) return;
        ActuatorRuntime_ReleaseCleanPulse(owner->pulse_token);
        owner->active = 0u;
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_ACTIVE);
    }
    if (owner->final_state == 1u) {
        if (McuWorkPreparation_PollMeasurement(preparation, endpoint, now,
            ECOBIN_UART_MESSAGE_CLEAN_FINAL_WEIGHT_READY, owner->action_sequence,
            &owner->final_measurement, &owner->final_meta) != 2u) return;
        owner->final_state = 2u;
        complete(owner, endpoint, available(&owner->final_measurement)
            ? ECOBIN_UART_WORK_RESULT_FINISH_REASON_CLEAN_CONFIRMED : ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED,
            owner->final_meta.observed_uptime_ms);
    }
}

uint8_t McuCleanExecution_Request(McuCleanExecution *owner, McuControlEndpoint *endpoint,
    const uint8_t *operation_uid, uint8_t message, uint16_t after_action_sequence, uint64_t now) {
    McuWorkPreparation *preparation;
    ActuatorSnapshot snapshot;
    McuConfigWeightPolicy policy;
    uint32_t sequence;
    if (owner == NULL || endpoint == NULL || operation_uid == NULL || owner->preparation == NULL
        || (message != ECOBIN_UART_MESSAGE_CLEAN_UNLOCK_REQUESTED && message != ECOBIN_UART_MESSAGE_CLEAN_FINISH_REQUESTED)
        || !context_valid(owner, endpoint, now) || owner->active || !owner->started || owner->final_state
        || endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_ACTIVE
        || owner->action_sequence != after_action_sequence || owner->action_sequence == UINT16_MAX
        || now >= owner->operation_deadline_ms) return 0u;
    preparation = owner->preparation;
    snapshot = ActuatorRuntime_Snapshot();
    if (memcmp(operation_uid, preparation->start_payload + START(OPERATION_UID), 16u) != 0
        || !McuOpeningGate_ConfigurationMatches(preparation, endpoint)
        || snapshot.update_latched || snapshot.lock_powered || !snapshot.door.target_valid
        || !snapshot.door.action_active || snapshot.door.target != MCU_DIRECTION_CLOSE) return 0u;
    owner->last_now_ms = owner->intent_at_ms = now;
    ++owner->action_sequence;
    if (message == ECOBIN_UART_MESSAGE_CLEAN_UNLOCK_REQUESTED) {
        if (!begin_pulse(owner, endpoint, owner->operation_deadline_ms))
            complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_CANCELLED, now);
    } else {
        /* This is the actual user's completion button, not an inferred state
         * from GPIO/polling. Keep this fact even if the new weight cannot read. */
        owner->physical_close_confirmed = 1u;
        policy = preparation->weight.policy;
        sequence = preparation->weight.measurement.result.measurement_id;
        if (sequence == UINT32_MAX || !McuWeightRun_Begin(&preparation->weight, &policy, sequence + 1u, now)) {
            complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_CANCELLED, now);
        } else {
            owner->final_state = 1u;
            McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_FINAL_MEASURING);
        }
    }
    return 1u;
}

uint8_t McuCleanExecution_Confirm(McuCleanExecution *owner, McuControlEndpoint *endpoint,
    const uint8_t *operation_uid, uint16_t action_sequence, const uint8_t *measurement_uid, uint64_t now) {
    (void)owner; (void)endpoint; (void)operation_uid; (void)action_sequence; (void)measurement_uid; (void)now;
    return 0u;
}
static uint8_t receive(McuControlEndpoint *endpoint, uint8_t message, const uint8_t *payload,
    size_t length, uint64_t received, void *context, McuSessionDecision *decision) {
    (void)endpoint; (void)message; (void)payload; (void)length; (void)received; (void)context; (void)decision;
    return 0u;
}
uint8_t McuCleanExecution_Attach(McuCleanExecution *owner,
    McuWorkPreparation *preparation, McuControlEndpoint *endpoint) {
    if (owner == NULL || !McuWorkPreparation_AttachActions(preparation, endpoint,
        ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION, receive, poll, owner)) return 0u;
    memset(owner, 0, sizeof(*owner));
    owner->preparation = preparation;
    return 1u;
}
