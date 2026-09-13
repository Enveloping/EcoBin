#include "mcu_delivery_execution.h"
#include "actuator_runtime.h"
#include <string.h>

#define START(field) ECOBIN_UART_START_DELIVERY_SESSION_##field##_OFFSET
#define DEVICE(field) (ECOBIN_UART_CONFIG_PREIMAGE_DEVICE_OFFSET + ECOBIN_UART_CONFIG_DEVICE_BLOCK_##field##_OFFSET - ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET)
#define MAX_UPTIME UINT64_C(9007199254740991)
typedef char delivery_execution_ram_budget[(sizeof(McuDeliveryExecution) <= 256u) ? 1 : -1];

static uint8_t available(const McuResultMeasurement *measurement) {
    return measurement->kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_STABLE_MEAN
        || measurement->kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_TIMEOUT_MEDIAN;
}

static uint8_t context_valid(McuDeliveryExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    McuWorkPreparation *preparation = owner->preparation;
    return (uint8_t)(!endpoint->feeding && endpoint->application_context == preparation
        && preparation->start_message == ECOBIN_UART_MESSAGE_START_DELIVERY_SESSION
        && endpoint->work.status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        && memcmp(endpoint->work.identity, preparation->start_payload, START(PORT_NO)) == 0
        && now >= endpoint->last_input_ms && now >= owner->last_now_ms
        && now <= MAX_UPTIME && now <= ActuatorRuntime_Snapshot().captured_uptime_ms);
}

static void complete(McuDeliveryExecution *owner, McuControlEndpoint *endpoint, uint8_t reason, uint64_t at) {
    McuResultSummary summary = {0};
    McuResultMeasurement not_taken = {0};
    ActuatorSnapshot snapshot = ActuatorRuntime_Snapshot();
    /* Recheck after measurement publication too: a control stop is not a
     * closed-door weight-only failure or normal ending. Preserve real weights. */
    if ((reason == ECOBIN_UART_WORK_RESULT_FINISH_REASON_DELIVERY_END
            || reason == ECOBIN_UART_WORK_RESULT_FINISH_REASON_DELIVERY_WINDOW_EXPIRED
            || (reason == ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED && owner->postclose_state == 3u
                && owner->postclose.kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_UNAVAILABLE))
        && (snapshot.update_latched || !snapshot.door.target_valid || !snapshot.door.action_active
            || snapshot.door.target != MCU_DIRECTION_CLOSE)) reason = ECOBIN_UART_WORK_RESULT_FINISH_REASON_CANCELLED;
    summary.config_version = owner->preparation->initial_meta.config_version;
    summary.completed_uptime_ms = at;
    summary.delivery_round_count = owner->round_index;
    summary.negative_weight_anomaly = owner->negative_weight_anomaly;
    summary.finish_reason = reason;
    McuResultBuilder_Complete(&endpoint->work, &summary, &owner->preparation->initial,
        owner->postclose_state == 3u ? &owner->postclose : &not_taken,
        owner->preparation->scratch, sizeof(owner->preparation->scratch));
}

static void begin_cycle(McuDeliveryExecution *owner, McuControlEndpoint *endpoint,
    uint64_t deadline, uint64_t now) {
    owner->cycle_token = ActuatorRuntime_BeginDeliveryCycle(deadline,
        ecobin_uart_read_u32_be(owner->preparation->start_payload + START(DELIVERY_AUTO_CLOSE_MS)));
    if (!owner->cycle_token) {
        complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED, now);
        return;
    }
    owner->active = 1u;
    owner->opened = 0u;
    McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COMMAND);
}

static void poll(McuControlEndpoint *endpoint, uint64_t now, void *context) {
    McuDeliveryExecution *owner = (McuDeliveryExecution *)context;
    McuWorkPreparation *preparation = owner->preparation;
    McuOpeningLimits limits;
    ActuatorDeliveryCycle cycle;
    ActuatorSnapshot snapshot;
    McuConfigWeightPolicy policy;
    uint32_t sequence, duration;
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
        owner->round_before_grams = preparation->initial.grams;
        owner->close_travel_wait_ms = ecobin_uart_read_u32_be(preparation->configuration.active.preimage + DEVICE(DELIVERY_DOOR_TRAVEL_WAIT_MS));
        begin_cycle(owner, endpoint, limits.execution_deadline_ms, now);
        return;
    }
    if (owner->active) {
        cycle = ActuatorRuntime_DeliveryCycle();
        if (!cycle.present || cycle.token != owner->cycle_token) {
            owner->active = 0u;
            complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED, now);
            return;
        }
        if (cycle.opened && !owner->opened) {
            owner->opened = 1u;
            ++owner->round_index;
            McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COUNTDOWN);
        }
        if (cycle.expired || cycle.interrupted) {
            ActuatorRuntime_ReleaseDeliveryCycle(owner->cycle_token);
            owner->active = 0u;
            complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_CANCELLED, cycle.terminal_at_ms);
            return;
        }
        if (!cycle.closed) return;
        owner->closed_at_ms = cycle.closed_at_ms;
        owner->postclose_state = 1u;
        ActuatorRuntime_ReleaseDeliveryCycle(owner->cycle_token);
        owner->active = 0u;
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_CLOSE_TRAVEL_WAIT);
    }
    /* Timer can dispatch CLOSE between the cycle read and foreground phase
     * update. Inspect the current control snapshot, never an earlier OPEN. */
    snapshot = ActuatorRuntime_Snapshot();
    /* PB5 pause keeps CLOSE and is not a position fault. Actual update/context
     * loss ends the work without reconstructing mechanical recovery. */
    if (snapshot.update_latched || !snapshot.door.target_valid || !snapshot.door.action_active
        || snapshot.door.target != MCU_DIRECTION_CLOSE) owner->interrupted = 1u;
    if (owner->interrupted) {
        if (owner->postclose_state == 2u) {
            if (!McuWorkPreparation_InterruptMeasurement(preparation, now)
                || McuWorkPreparation_PollMeasurement(preparation, endpoint, now,
                    ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY, owner->round_index,
                    &owner->postclose, &owner->postclose_meta) != 2u) return;
            owner->postclose_state = 3u;
        }
        /* Cancellation preserves an already-terminal weight verbatim and
         * cannot be mistaken for a pure closed-door FAILED weight timeout. */
        complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_CANCELLED, now);
        return;
    }
    if (owner->postclose_state == 1u) {
        if (now < owner->closed_at_ms || now - owner->closed_at_ms < owner->close_travel_wait_ms) return;
        policy = preparation->weight.policy;
        sequence = preparation->weight.measurement.result.measurement_id;
        if (sequence == UINT32_MAX || !McuWeightRun_Begin(&preparation->weight, &policy, sequence + 1u, now)) {
            complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED, now);
            return;
        }
        owner->postclose_state = 2u;
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_POSTCLOSE_MEASURING);
    }
    if (owner->postclose_state == 2u) {
        if (McuWorkPreparation_PollMeasurement(preparation, endpoint, now,
            ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY, owner->round_index,
            &owner->postclose, &owner->postclose_meta) != 2u) return;
        owner->postclose_state = 3u;
        if (!available(&owner->postclose)) {
            complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED, owner->postclose_meta.observed_uptime_ms);
            return;
        }
        if ((int64_t)owner->round_before_grams - owner->postclose.grams >=
            (int64_t)ecobin_uart_read_u32_be(preparation->start_payload + START(NEGATIVE_WEIGHT_THRESHOLD_GRAMS)))
            owner->negative_weight_anomaly = 1u;
        duration = ecobin_uart_read_u32_be(preparation->start_payload + START(CONTINUE_DELIVERY_WAIT_MS));
        if (now > MAX_UPTIME - duration) {
            complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED, now);
            return;
        }
        owner->selection_deadline_ms = now + duration;
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_SELECTION);
    }
    if (owner->postclose_state != 3u) return;
    if (!owner->selection && now >= owner->selection_deadline_ms) {
        owner->selection = ECOBIN_UART_DELIVERY_SELECTION_WINDOW_EXPIRED;
        owner->selected_at_ms = owner->selection_deadline_ms;
    }
    if (owner->selection == ECOBIN_UART_DELIVERY_SELECTION_CONTINUE) {
        if (owner->round_index == UINT16_MAX || !McuOpeningGate_ConfigurationMatches(preparation, endpoint)) {
            complete(owner, endpoint, ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED, now);
            return;
        }
        owner->round_before_grams = owner->postclose.grams;
        owner->postclose_state = 0u;
        owner->selection = 0u;
        owner->selection_deadline_ms = owner->selected_at_ms = 0u;
        memset(&owner->postclose, 0, sizeof(owner->postclose));
        memset(&owner->postclose_meta, 0, sizeof(owner->postclose_meta));
        begin_cycle(owner, endpoint, MAX_UPTIME, now);
    } else if (owner->selection) {
        complete(owner, endpoint, owner->selection == ECOBIN_UART_DELIVERY_SELECTION_END
            ? ECOBIN_UART_WORK_RESULT_FINISH_REASON_DELIVERY_END : ECOBIN_UART_WORK_RESULT_FINISH_REASON_DELIVERY_WINDOW_EXPIRED,
            owner->selected_at_ms);
    }
}

uint8_t McuDeliveryExecution_CloseCurrent(McuDeliveryExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    if (owner == NULL || endpoint == NULL || owner->preparation == NULL
        || !context_valid(owner, endpoint, now) || !owner->active) return 0u;
    return ActuatorRuntime_RequestDeliveryClose(owner->cycle_token);
}

uint8_t McuDeliveryExecution_Select(McuDeliveryExecution *owner, McuControlEndpoint *endpoint,
    const uint8_t *measurement_uid, uint8_t selection, uint64_t now) {
    if (owner == NULL || endpoint == NULL || owner->preparation == NULL || measurement_uid == NULL
        || (selection != ECOBIN_UART_DELIVERY_SELECTION_CONTINUE && selection != ECOBIN_UART_DELIVERY_SELECTION_END)
        || !context_valid(owner, endpoint, now) || owner->active || owner->postclose_state != 3u
        || endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_SELECTION
        || owner->selection || now >= owner->selection_deadline_ms
        || memcmp(measurement_uid, owner->postclose.uid, 16u) != 0) return 0u;
    owner->selection = selection;
    owner->selected_at_ms = now;
    poll(endpoint, now, owner);
    return 1u;
}

/* Old per-action commands fall through to the endpoint's explicit unsupported
 * decision. There is no hidden second authorization path. */
static uint8_t receive(McuControlEndpoint *endpoint, uint8_t message, const uint8_t *payload,
    size_t length, uint64_t received, void *context, McuSessionDecision *decision) {
    (void)endpoint; (void)message; (void)payload; (void)length; (void)received; (void)context; (void)decision;
    return 0u;
}

uint8_t McuDeliveryExecution_Attach(McuDeliveryExecution *owner,
    McuWorkPreparation *preparation, McuControlEndpoint *endpoint) {
    if (owner == NULL || !McuWorkPreparation_AttachActions(preparation, endpoint,
        ECOBIN_UART_WORK_TYPE_DELIVERY_SESSION, receive, poll, owner)) return 0u;
    memset(owner, 0, sizeof(*owner));
    owner->preparation = preparation;
    return 1u;
}
