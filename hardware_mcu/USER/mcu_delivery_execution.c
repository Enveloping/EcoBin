#include "mcu_delivery_execution.h"
#include "actuator_runtime.h"
#include <string.h>

#define GRANT(field) ECOBIN_UART_AUTHORIZE_DELIVERY_FIRST_OPEN_##field##_OFFSET
#define EVENT(field) ECOBIN_UART_DELIVERY_DOOR_COMMAND_RESULT_##field##_OFFSET
#define START(field) ECOBIN_UART_START_DELIVERY_SESSION_##field##_OFFSET
#define CHOICE(field) ECOBIN_UART_DELIVERY_SELECTION_##field##_OFFSET
#define ABORT(field) ECOBIN_UART_DELIVERY_CYCLE_ABORTED_##field##_OFFSET
#define INTERRUPT(field) ECOBIN_UART_DELIVERY_POSTCLOSE_INTERRUPTED_##field##_OFFSET
#define SCOPE(field) (ECOBIN_UART_QUERY_PROCESS_EVENT_##field##_OFFSET - 8u)
#define DEVICE(field) (ECOBIN_UART_CONFIG_PREIMAGE_DEVICE_OFFSET + ECOBIN_UART_CONFIG_DEVICE_BLOCK_##field##_OFFSET - ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET)

typedef char delivery_execution_ram_budget[(sizeof(McuDeliveryExecution) <= 512u) ? 1 : -1];
#define SHARED_OUTPUT_FIELD(field) typedef char local_output_##field##_offset_matches[( \
    EVENT(field) == ECOBIN_UART_DELIVERY_LOCAL_DOOR_RESULT_##field##_OFFSET) ? 1 : -1]
SHARED_OUTPUT_FIELD(MCU_BOOT_ID);
SHARED_OUTPUT_FIELD(MCU_EVENT_SEQUENCE);
SHARED_OUTPUT_FIELD(UPTIME_MS);
SHARED_OUTPUT_FIELD(MCU_COMMAND_UID);
SHARED_OUTPUT_FIELD(SESSION_UID);
SHARED_OUTPUT_FIELD(PORT_NO);
SHARED_OUTPUT_FIELD(ROUND_INDEX);
SHARED_OUTPUT_FIELD(COMMAND);
SHARED_OUTPUT_FIELD(OUTPUT_STATUS);
SHARED_OUTPUT_FIELD(PHYSICAL_DOOR_STATE_BASIS);
SHARED_OUTPUT_FIELD(FAULT_CODE);
#undef SHARED_OUTPUT_FIELD

static void cancel_unused_interruption(McuDeliveryExecution *owner, McuControlEndpoint *endpoint);

static uint8_t publish(McuDeliveryExecution *owner, McuControlEndpoint *endpoint,
    uint8_t close, uint64_t at, uint8_t rejected) {
    uint8_t body[ECOBIN_UART_DELIVERY_LOCAL_DOOR_RESULT_PAYLOAD_MAX_LENGTH];
    uint8_t message = owner->local_cause_sequence ? ECOBIN_UART_MESSAGE_DELIVERY_LOCAL_DOOR_RESULT
        : ECOBIN_UART_MESSAGE_DELIVERY_DOOR_COMMAND_RESULT;
    size_t length = owner->local_cause_sequence ? sizeof(body) : ECOBIN_UART_DELIVERY_DOOR_COMMAND_RESULT_PAYLOAD_MAX_LENGTH;
    memset(body, 0, sizeof(body));
    ecobin_uart_write_u64_be(body + EVENT(MCU_BOOT_ID), endpoint->session.boot_id);
    ecobin_uart_write_u64_be(body + EVENT(UPTIME_MS), at);
    memcpy(body + EVENT(MCU_COMMAND_UID), owner->grant + GRANT(MCU_COMMAND_UID), 16u);
    memcpy(body + EVENT(SESSION_UID), owner->grant + GRANT(SESSION_UID), 16u);
    body[EVENT(PORT_NO)] = owner->grant[GRANT(PORT_NO)];
    ecobin_uart_write_u16_be(body + EVENT(ROUND_INDEX), owner->round_index);
    body[EVENT(COMMAND)] = close ? ECOBIN_UART_DELIVERY_DOOR_COMMAND_CLOSE : ECOBIN_UART_DELIVERY_DOOR_COMMAND_OPEN;
    body[EVENT(OUTPUT_STATUS)] = rejected ? ECOBIN_UART_DOOR_COMMAND_OUTPUT_STATUS_OUTPUT_REJECTED
        : ECOBIN_UART_DOOR_COMMAND_OUTPUT_STATUS_COMMAND_DISPATCHED;
    body[EVENT(PHYSICAL_DOOR_STATE_BASIS)] = ECOBIN_UART_DOOR_PHYSICAL_STATE_BASIS_NOT_OBSERVABLE;
    ecobin_uart_write_u16_be(body + EVENT(FAULT_CODE), rejected ? ECOBIN_UART_FAULT_CODE_DELIVERY_DOOR_OUTPUT_REJECTED
        : ECOBIN_UART_FAULT_CODE_NONE);
    if (owner->local_cause_sequence) ecobin_uart_write_u32_be(body
        + ECOBIN_UART_DELIVERY_LOCAL_DOOR_RESULT_SELECTION_EVENT_SEQUENCE_OFFSET, owner->local_cause_sequence);
    return McuControlEndpoint_PublishActuatorEvent(endpoint, close ? &owner->close_record : &owner->open_record,
        0u, message, body, length) != 0u;
}

static uint8_t publish_abort(McuDeliveryExecution *owner, McuControlEndpoint *endpoint,
    uint8_t reason, uint8_t opened, uint64_t at) {
    uint8_t body[ECOBIN_UART_DELIVERY_CYCLE_ABORTED_PAYLOAD_MAX_LENGTH];
    if (owner->close_published) return 0u; /* Cannot call an actual CLOSE an abort. */
    memset(body, 0, sizeof(body));
    ecobin_uart_write_u64_be(body + ABORT(MCU_BOOT_ID), endpoint->session.boot_id);
    ecobin_uart_write_u64_be(body + ABORT(UPTIME_MS), at);
    memcpy(body + ABORT(MCU_COMMAND_UID), owner->grant + GRANT(MCU_COMMAND_UID), 16u);
    memcpy(body + ABORT(SESSION_UID), owner->grant + GRANT(SESSION_UID), 16u);
    body[ABORT(PORT_NO)] = owner->grant[GRANT(PORT_NO)];
    ecobin_uart_write_u16_be(body + ABORT(ROUND_INDEX), owner->round_index);
    body[ABORT(ABORT_REASON)] = reason;
    body[ABORT(OPEN_DISPATCHED)] = opened;
    ecobin_uart_write_u32_be(body + ABORT(SELECTION_EVENT_SEQUENCE), owner->local_cause_sequence);
    if (!McuControlEndpoint_PublishActuatorEvent(endpoint, &owner->close_record, 0u,
        ECOBIN_UART_MESSAGE_DELIVERY_CYCLE_ABORTED, body, sizeof(body))) return 0u;
    owner->abort_reason = reason;
    owner->abort_opened = opened;
    owner->aborted_at_ms = at;
    McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_SAFETY_LOCKED);
    /* This frozen pre-CLOSE abort excludes a later CLOSE in this work. The
     * separately reserved post-CLOSE interruption can no longer be needed. */
    cancel_unused_interruption(owner, endpoint);
    return 1u;
}

static void poll_cycle(McuControlEndpoint *endpoint, uint64_t now, void *context) {
    McuDeliveryExecution *owner = (McuDeliveryExecution *)context;
    ActuatorDeliveryCycle cycle;
    if (!owner->active || now < endpoint->last_input_ms) return;
    cycle = ActuatorRuntime_DeliveryCycle();
    if (owner->cycle_token == 0u) {
        if (now < owner->rejected_at_ms || !publish(owner, endpoint, 0u, owner->rejected_at_ms, 1u)) return;
        owner->open_published = 1u;
        if (!publish_abort(owner, endpoint, ECOBIN_UART_DELIVERY_CYCLE_ABORT_REASON_CYCLE_START_REJECTED,
            0u, owner->rejected_at_ms)) return;
        owner->active = 0u;
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_SAFETY_LOCKED);
        return;
    }
    if (!cycle.present || cycle.token != owner->cycle_token) return; /* Never substitute another run. */
    if (cycle.opened && !owner->open_published) {
        if (now < cycle.opened_at_ms || !publish(owner, endpoint, 0u, cycle.opened_at_ms, 0u)) return;
        owner->open_published = 1u;
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COUNTDOWN);
    }
    if (cycle.closed && !owner->close_published) {
        if (now < cycle.closed_at_ms || !publish(owner, endpoint, 1u, cycle.closed_at_ms, 0u)) return;
        owner->close_published = 1u;
        owner->closed_at_ms = cycle.closed_at_ms;
        owner->postclose_state = 1u;
        /* Still occupied; a logical CLOSE is not a final measurement/result. */
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_CLOSE_TRAVEL_WAIT);
    }
    if (cycle.expired || cycle.interrupted) {
        if (now < cycle.terminal_at_ms) return;
        if (!owner->open_published) {
            if (!publish(owner, endpoint, 0u, cycle.terminal_at_ms, 1u)) return;
            owner->open_published = 1u;
        }
        /* The separately reserved closing position holds either actual CLOSE
         * or this terminal abort, never both and never an invented close. */
        if (!publish_abort(owner, endpoint, cycle.expired ? ECOBIN_UART_DELIVERY_CYCLE_ABORT_REASON_OPEN_DEADLINE_EXPIRED
            : ECOBIN_UART_DELIVERY_CYCLE_ABORT_REASON_UPDATE_STOPPED, cycle.opened, cycle.terminal_at_ms)) return;
    }
    if (cycle.closed || cycle.expired || cycle.interrupted) {
        if (ActuatorRuntime_ReleaseDeliveryCycle(owner->cycle_token)) owner->active = 0u;
    }
}

static void remember_weight_anomaly(McuDeliveryExecution *owner) {
    if ((owner->postclose.kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_STABLE_MEAN
            || owner->postclose.kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_TIMEOUT_MEDIAN)
        && (int64_t)owner->round_before_grams - (int64_t)owner->postclose.grams
            >= (int64_t)ecobin_uart_read_u32_be(owner->preparation->start_payload + START(NEGATIVE_WEIGHT_THRESHOLD_GRAMS)))
        owner->negative_weight_anomaly = 1u;
}

static void poll_postclose(McuDeliveryExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    McuWorkPreparation *preparation = owner->preparation;
    ActuatorSnapshot snapshot;
    McuConfigWeightPolicy policy;
    uint32_t sequence;
    if (owner->active || owner->interruption_reason || owner->postclose_state == 0u || owner->postclose_state == 3u
        || now < endpoint->last_input_ms || now < owner->closed_at_ms
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING) return;
    if (owner->postclose_state == 1u) {
        if (endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_CLOSE_TRAVEL_WAIT) return;
        snapshot = ActuatorRuntime_Snapshot();
        if (now > snapshot.captured_uptime_ms) return;
        if (snapshot.update_latched || !snapshot.door.target_valid || !snapshot.door.action_active
            || snapshot.door.target != MCU_DIRECTION_CLOSE) {
            McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_SAFETY_LOCKED);
            return;
        }
        /* Fixed configured travel wait, NOT PB5/physical-position proof. Start
         * a fresh five-second window at actual foreground start, not in the past. */
        if (now - owner->closed_at_ms < owner->close_travel_wait_ms || endpoint->process_event.held
            || !preparation->weight.retired) return;
        policy = preparation->weight.policy; /* Original accepted work policy. */
        sequence = preparation->weight.measurement.result.measurement_id;
        if (sequence == UINT32_MAX || !McuWeightRun_Begin(&preparation->weight, &policy, sequence + 1u, now)) return;
        owner->postclose_state = 2u;
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_POSTCLOSE_MEASURING);
    }
    if (McuWorkPreparation_PollMeasurement(preparation, endpoint, now,
        ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY, owner->round_index, &owner->postclose, &owner->postclose_meta) != 2u) return;
    owner->postclose_state = 3u;
    remember_weight_anomaly(owner);
    McuWorkState_SetPhase(&endpoint->work, owner->postclose.kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_STABLE_MEAN
        || owner->postclose.kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_TIMEOUT_MEDIAN
        ? ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_SELECTION : ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_FINALIZING);
}

static void process_scope(const McuDeliveryExecution *owner, const McuControlEndpoint *endpoint,
    uint8_t message, uint8_t *scope) {
    memcpy(scope, endpoint->work.identity, MCU_WORK_IDENTITY_LENGTH);
    scope[SCOPE(EVENT_MESSAGE_TYPE)] = message;
    ecobin_uart_write_u16_be(scope + SCOPE(STEP_SEQUENCE), owner->postclose_meta.step_sequence);
    ecobin_uart_write_u64_be(scope + SCOPE(CONFIG_VERSION), owner->postclose_meta.config_version);
}

static void choice_payload(const McuDeliveryExecution *owner, uint8_t *body) {
    const uint8_t *start = owner->preparation->start_payload;
    memset(body, 0, ECOBIN_UART_DELIVERY_SELECTION_PAYLOAD_MAX_LENGTH);
    ecobin_uart_write_u64_be(body + CHOICE(MCU_BOOT_ID), owner->postclose.source_boot_id);
    ecobin_uart_write_u32_be(body + CHOICE(MCU_EVENT_SEQUENCE), owner->selection_event_sequence);
    ecobin_uart_write_u64_be(body + CHOICE(UPTIME_MS), owner->selected_at_ms);
    memcpy(body + CHOICE(MCU_COMMAND_UID), start + START(MCU_COMMAND_UID), 16u);
    memcpy(body + CHOICE(SESSION_UID), start + START(SESSION_UID), 16u);
    body[CHOICE(PORT_NO)] = start[START(PORT_NO)];
    ecobin_uart_write_u16_be(body + CHOICE(ROUND_INDEX), owner->postclose_meta.step_sequence);
    memcpy(body + CHOICE(POST_CLOSE_MEASUREMENT_UID), owner->postclose.uid, 16u);
    ecobin_uart_write_u64_be(body + CHOICE(CONFIG_VERSION), owner->postclose_meta.config_version);
    body[CHOICE(SELECTION)] = owner->selection;
}

static uint8_t process_saved(McuDeliveryExecution *owner, McuControlEndpoint *endpoint, uint8_t message) {
    const McuProcessEventSlot *slot = &endpoint->process_event;
    uint8_t scope[sizeof(slot->scope)];
    size_t length;
    if (slot->held || slot->boot_id != endpoint->session.boot_id) return 0u;
    process_scope(owner, endpoint, message, scope);
    if (memcmp(scope, slot->scope, sizeof(scope)) != 0) return 0u;
    if (message == ECOBIN_UART_MESSAGE_DELIVERY_SELECTION) {
        if (owner->selection_event_sequence == 0u) return 0u;
        choice_payload(owner, owner->preparation->scratch);
        length = ECOBIN_UART_DELIVERY_SELECTION_PAYLOAD_MAX_LENGTH;
    } else {
        /* The encoder only produces records while MEASURING. Here inspect the
         * already validated retained identity; never pretend the old phase runs. */
        return (uint8_t)(slot->length == ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_PAYLOAD_MAX_LENGTH
            && slot->highest_sequence == owner->postclose.event_sequence && slot->highest_sequence != 0u
            && memcmp(slot->payload + ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_MEASUREMENT_UID_OFFSET,
                owner->postclose.uid, 16u) == 0
            && ecobin_uart_read_u64_be(slot->payload + ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_UPTIME_MS_OFFSET)
                == owner->postclose_meta.observed_uptime_ms);
    }
    return (uint8_t)(length != 0u && length == slot->length
        && memcmp(slot->payload, owner->preparation->scratch, length) == 0);
}

static uint8_t delivery_context(const McuDeliveryExecution *owner, const McuControlEndpoint *endpoint, uint64_t now) {
    const McuWorkPreparation *preparation = owner->preparation;
    return (uint8_t)(endpoint->application_context == preparation && !endpoint->feeding && !owner->active && !owner->interruption_reason
        && owner->postclose_state == 3u && preparation->start_message == ECOBIN_UART_MESSAGE_START_DELIVERY_SESSION
        && endpoint->work.status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        && endpoint->session.boot_id == owner->postclose.source_boot_id
        && memcmp(endpoint->work.identity, preparation->start_payload, START(PORT_NO)) == 0
        && memcmp(owner->grant + GRANT(SESSION_UID), preparation->start_payload + START(SESSION_UID), 16u) == 0
        && now >= endpoint->last_input_ms && now >= owner->selection_last_now_ms
        && now >= owner->postclose_meta.observed_uptime_ms && now <= UINT64_C(9007199254740991)
        && now <= ActuatorRuntime_Snapshot().captured_uptime_ms);
}

static void poll_selection(McuDeliveryExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    uint8_t scope[sizeof(endpoint->process_event.scope)];
    uint32_t duration;
    if (!delivery_context(owner, endpoint, now)
        || endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_SELECTION) return;
    owner->selection_last_now_ms = now;
    if (owner->selection_deadline_ms == 0u) {
        if (!process_saved(owner, endpoint, ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY)) return;
        duration = ecobin_uart_read_u32_be(owner->preparation->start_payload + START(CONTINUE_DELIVERY_WAIT_MS));
        if (now > UINT64_C(9007199254740991) - duration) return;
        owner->selection_deadline_ms = now + duration;
    }
    if (owner->selection == 0u && now >= owner->selection_deadline_ms) {
        owner->selection = ECOBIN_UART_DELIVERY_SELECTION_WINDOW_EXPIRED;
        owner->selected_at_ms = owner->selection_deadline_ms; /* Poll delay does not renew the window. */
    }
    if (owner->selection == 0u || owner->selection_event_sequence != 0u) return;
    owner->selection_event_sequence = McuControlEndpoint_ReserveEventSequence(endpoint);
    if (owner->selection_event_sequence == 0u) return;
    process_scope(owner, endpoint, ECOBIN_UART_MESSAGE_DELIVERY_SELECTION, scope);
    choice_payload(owner, owner->preparation->scratch);
    McuProcessEventSlot_Freeze(&endpoint->process_event, scope, sizeof(scope),
        ECOBIN_UART_MESSAGE_DELIVERY_SELECTION, owner->preparation->scratch, ECOBIN_UART_DELIVERY_SELECTION_PAYLOAD_MAX_LENGTH);
}

static void poll_continue(McuDeliveryExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    McuWorkPreparation *preparation = owner->preparation;
    ActuatorSnapshot snapshot;
    McuActuatorEventReservation open_record = {0}, close_record = {0};
    if (!delivery_context(owner, endpoint, now)
        || endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_SELECTION
        || owner->selection != ECOBIN_UART_DELIVERY_SELECTION_CONTINUE
        || !process_saved(owner, endpoint, ECOBIN_UART_MESSAGE_DELIVERY_SELECTION)) return;
    snapshot = ActuatorRuntime_Snapshot();
    if (owner->round_index == UINT16_MAX || now > UINT64_C(9007199254740991) - 100u
        || snapshot.update_latched || snapshot.lock_powered
        || !snapshot.door.target_valid || !snapshot.door.action_active || snapshot.door.target != MCU_DIRECTION_CLOSE
        || !McuOpeningGate_ConfigurationMatches(preparation, endpoint)
        || preparation->guard == NULL || preparation->guard(ECOBIN_UART_MESSAGE_DELIVERY_SELECTION,
            preparation->scratch, ECOBIN_UART_DELIVERY_SELECTION_PAYLOAD_MAX_LENGTH, now, preparation->guard_context)
                != ECOBIN_UART_NACK_ERROR_NONE) {
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_SAFETY_LOCKED);
        return;
    }
    if (ActuatorRuntime_DeliveryCycle().present || !preparation->weight.retired || preparation->weight.in_flight) return;
    if (!McuControlEndpoint_ReserveActuatorEvents(endpoint, 1u, &open_record)) return;
    if (!McuControlEndpoint_ReserveActuatorEvents(endpoint, 1u, &close_record)) {
        McuControlEndpoint_CancelActuatorEvents(endpoint, &open_record);
        return;
    }
    owner->local_cause_sequence = owner->selection_event_sequence;
    owner->round_before_grams = owner->postclose.grams;
    ++owner->round_index;
    owner->open_record = open_record;
    owner->close_record = close_record;
    owner->active = 1u;
    owner->open_published = owner->close_published = owner->postclose_state = 0u;
    owner->selection = 0u;
    owner->selection_event_sequence = 0u;
    owner->selection_deadline_ms = owner->selected_at_ms = 0u;
    memset(&owner->postclose, 0, sizeof(owner->postclose));
    memset(&owner->postclose_meta, 0, sizeof(owner->postclose_meta));
    /* The window limited the original BUTTON, not receipt delivery. This is a
     * local action in the same retained work, not renewal of the expired first
     * Pi grant. Use only the representable uptime ceiling for the cycle timer;
     * configuration/safety/identity are rechecked above before this one start. */
    owner->cycle_token = ActuatorRuntime_BeginDeliveryCycle(UINT64_C(9007199254740991),
        ecobin_uart_read_u32_be(preparation->start_payload + START(DELIVERY_AUTO_CLOSE_MS)));
    owner->rejected_at_ms = ActuatorRuntime_Snapshot().captured_uptime_ms;
    McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COMMAND);
}

static void cancel_unused_interruption(McuDeliveryExecution *owner, McuControlEndpoint *endpoint) {
    if (McuControlEndpoint_CancelActuatorEvents(endpoint, &owner->interruption_record))
        memset(&owner->interruption_record, 0, sizeof(owner->interruption_record));
}

static void poll_final(McuDeliveryExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    McuResultSummary summary;
    ActuatorSnapshot snapshot;
    uint64_t fullness_completed;
    uint8_t failed;
    if (!delivery_context(owner, endpoint, now)) return;
    failed = owner->postclose.kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_UNAVAILABLE
        || owner->postclose.kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_INTERRUPTED;
    if (failed) {
        if (endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_FINALIZING
            || !process_saved(owner, endpoint, ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY)) return;
    } else if (endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_SELECTION
        || (owner->selection != ECOBIN_UART_DELIVERY_SELECTION_END
            && owner->selection != ECOBIN_UART_DELIVERY_SELECTION_WINDOW_EXPIRED)
        || !process_saved(owner, endpoint, ECOBIN_UART_MESSAGE_DELIVERY_SELECTION)) return;
    owner->selection_last_now_ms = now;
    snapshot = ActuatorRuntime_Snapshot();
    if (now > snapshot.captured_uptime_ms) return;
    /* Logical command state only. PB5 may pause CLOSE at the bottom and is
     * neither a physical position signal nor by itself a terminal fault. */
    if (snapshot.update_latched || !owner->close_published || !snapshot.door.target_valid
        || !snapshot.door.action_active || snapshot.door.target != MCU_DIRECTION_CLOSE) {
        McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_SAFETY_LOCKED);
        return;
    }
    memset(&summary, 0, sizeof(summary));
    summary.config_version = owner->postclose_meta.config_version;
    summary.completed_uptime_ms = failed ? owner->postclose_meta.observed_uptime_ms : owner->selected_at_ms;
    if (failed) {
        /* process_saved above pins this exact post-close record. Its sources
         * end independently: failure is complete only after both have ended.
         * Keep the original weight time/elapsed and never use receipt latency
         * or a later poll as a new completion time. NOT_SAMPLED has zero time. */
        fullness_completed = ecobin_uart_read_u64_be(endpoint->process_event.payload
            + ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_FULLNESS_COMPLETED_UPTIME_MS_OFFSET);
        if (fullness_completed > summary.completed_uptime_ms)
            summary.completed_uptime_ms = fullness_completed;
    }
    summary.delivery_round_count = (uint16_t)owner->postclose_meta.step_sequence;
    summary.negative_weight_anomaly = owner->negative_weight_anomaly;
    summary.finish_reason = failed ? ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED
        : owner->selection == ECOBIN_UART_DELIVERY_SELECTION_END
            ? ECOBIN_UART_WORK_RESULT_FINISH_REASON_DELIVERY_END : ECOBIN_UART_WORK_RESULT_FINISH_REASON_DELIVERY_WINDOW_EXPIRED;
    if (McuResultBuilder_Complete(&endpoint->work, &summary, &owner->preparation->initial,
        &owner->postclose, owner->preparation->scratch, sizeof(owner->preparation->scratch)))
        cancel_unused_interruption(owner, endpoint);
}

uint8_t McuDeliveryExecution_Select(McuDeliveryExecution *owner, McuControlEndpoint *endpoint,
    const uint8_t *postclose_measurement_uid, uint8_t selection, uint64_t now) {
    if (owner == NULL || endpoint == NULL || owner->preparation == NULL || postclose_measurement_uid == NULL
        || (selection != ECOBIN_UART_DELIVERY_SELECTION_CONTINUE && selection != ECOBIN_UART_DELIVERY_SELECTION_END)
        || !delivery_context(owner, endpoint, now)
        || endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_SELECTION || owner->selection_deadline_ms == 0u
        || owner->selection != 0u || memcmp(postclose_measurement_uid, owner->postclose.uid, 16u) != 0) return 0u;
    if (now >= owner->selection_deadline_ms) {
        poll_selection(owner, endpoint, now);
        return 0u;
    }
    owner->selection = selection;
    owner->selected_at_ms = now;
    poll_selection(owner, endpoint, now);
    return 1u;
}

static void poll_initial_failure(McuDeliveryExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    McuWorkPreparation *preparation = owner->preparation;
    McuResultSummary summary;
    McuResultMeasurement not_taken;
    const uint8_t *start = preparation->start_payload;
    if (endpoint->feeding || endpoint->application_context != preparation || owner->active
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_FINALIZING
        || preparation->start_message != ECOBIN_UART_MESSAGE_START_DELIVERY_SESSION
        || !preparation->initial_ready || !preparation->weight.retired || preparation->weight.in_flight
        || (preparation->initial.kind != ECOBIN_UART_RESULT_MEASUREMENT_KIND_UNAVAILABLE
            && preparation->initial.kind != ECOBIN_UART_RESULT_MEASUREMENT_KIND_INTERRUPTED)
        || preparation->initial.source_boot_id != endpoint->session.boot_id
        || preparation->initial_meta.config_version != ecobin_uart_read_u64_be(start + START(CONFIG_VERSION))
        || memcmp(endpoint->work.identity, start, START(PORT_NO)) != 0
        || endpoint->work.identity[MCU_WORK_IDENTITY_LENGTH - 2u] != ECOBIN_UART_WORK_TYPE_DELIVERY_SESSION
        || endpoint->work.identity[MCU_WORK_IDENTITY_LENGTH - 1u] != start[START(PORT_NO)]
        || now < endpoint->last_input_ms || now < preparation->weight.last_now_ms
        || now < preparation->initial_meta.observed_uptime_ms || now > ActuatorRuntime_Snapshot().captured_uptime_ms
        || !McuOpeningGate_InitialSaved(preparation, endpoint, 0u)) return;
    /* Failed before the first authorization: zero completed door rounds, but a
     * real attempted initial measurement. Do not invent a final measurement,
     * reuse the preceding business's executor fields or assert a safe door. */
    memset(&summary, 0, sizeof(summary));
    memset(&not_taken, 0, sizeof(not_taken));
    summary.config_version = preparation->initial_meta.config_version;
    summary.completed_uptime_ms = preparation->initial_meta.observed_uptime_ms;
    summary.finish_reason = ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED;
    McuResultBuilder_Complete(&endpoint->work, &summary, &preparation->initial, &not_taken,
        preparation->scratch, sizeof(preparation->scratch));
}

static void poll_abort(McuDeliveryExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    McuWorkPreparation *preparation = owner->preparation;
    McuResultSummary summary;
    McuResultMeasurement not_taken;
    uint8_t output_message = owner->local_cause_sequence ? ECOBIN_UART_MESSAGE_DELIVERY_LOCAL_DOOR_RESULT
        : ECOBIN_UART_MESSAGE_DELIVERY_DOOR_COMMAND_RESULT;
    if (!owner->abort_reason || owner->active || endpoint->feeding || endpoint->application_context != preparation
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_SAFETY_LOCKED
        || preparation->start_message != ECOBIN_UART_MESSAGE_START_DELIVERY_SESSION
        || memcmp(endpoint->work.identity, preparation->start_payload, START(PORT_NO)) != 0
        || memcmp(owner->grant + GRANT(SESSION_UID), preparation->start_payload + START(SESSION_UID), 16u) != 0
        || ecobin_uart_read_u64_be(owner->grant + GRANT(TARGET_MCU_BOOT_ID)) != endpoint->session.boot_id
        || owner->round_index == 0u || !preparation->initial_ready
        || !preparation->weight.retired || preparation->weight.in_flight
        || now < endpoint->last_input_ms || now < owner->aborted_at_ms || now < preparation->weight.last_now_ms
        || now > ActuatorRuntime_Snapshot().captured_uptime_ms
        || !McuActuatorEventJournal_MemberSaved(&endpoint->actuator_events, &owner->open_record, 0u, output_message)
        || !McuActuatorEventJournal_MemberSaved(&endpoint->actuator_events, &owner->close_record, 0u, ECOBIN_UART_MESSAGE_DELIVERY_CYCLE_ABORTED)) return;
    memset(&summary, 0, sizeof(summary));
    memset(&not_taken, 0, sizeof(not_taken));
    summary.config_version = preparation->initial_meta.config_version;
    summary.completed_uptime_ms = owner->aborted_at_ms;
    summary.delivery_round_count = (uint16_t)(owner->round_index - (owner->abort_opened ? 0u : 1u));
    summary.negative_weight_anomaly = owner->negative_weight_anomaly;
    summary.finish_reason = ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED;
    if (McuResultBuilder_Complete(&endpoint->work, &summary, &preparation->initial, &not_taken,
        preparation->scratch, sizeof(preparation->scratch))) cancel_unused_interruption(owner, endpoint);
}

static void poll_postclose_interruption(McuDeliveryExecution *owner, McuControlEndpoint *endpoint, uint64_t now) {
    McuWorkPreparation *preparation = owner->preparation;
    ActuatorSnapshot snapshot = ActuatorRuntime_Snapshot();
    McuResultSummary summary;
    McuResultMeasurement not_taken;
    uint8_t body[ECOBIN_UART_DELIVERY_POSTCLOSE_INTERRUPTED_PAYLOAD_MAX_LENGTH];
    uint8_t output_message = owner->local_cause_sequence ? ECOBIN_UART_MESSAGE_DELIVERY_LOCAL_DOOR_RESULT
        : ECOBIN_UART_MESSAGE_DELIVERY_DOOR_COMMAND_RESULT;
    if (owner->active || !owner->close_published || owner->postclose_state == 0u || endpoint->feeding
        || endpoint->application_context != preparation || !preparation->initial_ready
        || preparation->start_message != ECOBIN_UART_MESSAGE_START_DELIVERY_SESSION
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || ecobin_uart_read_u64_be(owner->grant + GRANT(TARGET_MCU_BOOT_ID)) != endpoint->session.boot_id
        || memcmp(endpoint->work.identity, preparation->start_payload, START(PORT_NO)) != 0
        || memcmp(owner->grant + GRANT(SESSION_UID), preparation->start_payload + START(SESSION_UID), 16u) != 0
        || now < endpoint->last_input_ms || now < preparation->weight.last_now_ms
        || now < owner->closed_at_ms || now > snapshot.captured_uptime_ms) return;
    if (!owner->interruption_reason) {
        if (endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_CLOSE_TRAVEL_WAIT
            && endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_POSTCLOSE_MEASURING
            && endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_SELECTION
            && endpoint->work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_FINALIZING) return;
        if (!snapshot.update_latched && snapshot.door.target_valid && snapshot.door.action_active
            && snapshot.door.target == MCU_DIRECTION_CLOSE) return; /* PB5 alone is not a fault. */
        owner->interruption_reason = snapshot.update_latched ? ECOBIN_UART_POSTCLOSE_INTERRUPTION_REASON_UPDATE_STOPPED
            : ECOBIN_UART_POSTCLOSE_INTERRUPTION_REASON_CLOSE_CONTEXT_LOST;
        owner->interrupted_phase = endpoint->work.phase;
        owner->interrupted_at_ms = now; /* Foreground observation, not guessed ISR time. */
    }
    if (owner->postclose_state == 2u) {
        /* Preserve an already terminal/due result; otherwise freeze the actual
         * partial acquisition. Do not refresh its time on later publication. */
        if (!McuWorkPreparation_InterruptMeasurement(preparation, now)
            || McuWorkPreparation_PollMeasurement(preparation, endpoint, now,
                ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY, owner->round_index,
                &owner->postclose, &owner->postclose_meta) != 2u) return;
        owner->postclose_state = 3u;
        remember_weight_anomaly(owner);
    }
    McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_SAFETY_LOCKED);
    if (now < owner->interrupted_at_ms || !preparation->weight.retired || preparation->weight.in_flight) return;
    memset(body, 0, sizeof(body));
    ecobin_uart_write_u64_be(body + INTERRUPT(MCU_BOOT_ID), endpoint->session.boot_id);
    ecobin_uart_write_u64_be(body + INTERRUPT(UPTIME_MS), owner->interrupted_at_ms);
    memcpy(body + INTERRUPT(MCU_COMMAND_UID), owner->grant + GRANT(MCU_COMMAND_UID), 16u);
    memcpy(body + INTERRUPT(SESSION_UID), owner->grant + GRANT(SESSION_UID), 16u);
    body[INTERRUPT(PORT_NO)] = owner->grant[GRANT(PORT_NO)];
    ecobin_uart_write_u16_be(body + INTERRUPT(ROUND_INDEX), owner->round_index);
    body[INTERRUPT(INTERRUPTED_PHASE)] = owner->interrupted_phase;
    body[INTERRUPT(INTERRUPTION_REASON)] = owner->interruption_reason;
    ecobin_uart_write_u32_be(body + INTERRUPT(POST_CLOSE_MEASUREMENT_EVENT_SEQUENCE), owner->postclose.event_sequence);
    if (!McuControlEndpoint_PublishActuatorEvent(endpoint, &owner->interruption_record, 0u,
        ECOBIN_UART_MESSAGE_DELIVERY_POSTCLOSE_INTERRUPTED, body, sizeof(body))) return;
    if (!McuActuatorEventJournal_MemberSaved(&endpoint->actuator_events, &owner->open_record, 0u, output_message)
        || !McuActuatorEventJournal_MemberSaved(&endpoint->actuator_events, &owner->close_record, 0u, output_message)
        || !McuActuatorEventJournal_MemberSaved(&endpoint->actuator_events, &owner->interruption_record, 0u,
            ECOBIN_UART_MESSAGE_DELIVERY_POSTCLOSE_INTERRUPTED)) return;
    if (owner->postclose_state == 3u && !process_saved(owner, endpoint,
        owner->selection ? ECOBIN_UART_MESSAGE_DELIVERY_SELECTION : ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY)) return;
    memset(&summary, 0, sizeof(summary));
    memset(&not_taken, 0, sizeof(not_taken));
    summary.config_version = preparation->initial_meta.config_version;
    summary.completed_uptime_ms = owner->interrupted_at_ms;
    summary.delivery_round_count = owner->round_index;
    summary.negative_weight_anomaly = owner->negative_weight_anomaly;
    summary.finish_reason = ECOBIN_UART_WORK_RESULT_FINISH_REASON_FAILED;
    McuResultBuilder_Complete(&endpoint->work, &summary, &preparation->initial,
        owner->postclose_state == 3u ? &owner->postclose : &not_taken,
        preparation->scratch, sizeof(preparation->scratch));
}

static void poll(McuControlEndpoint *endpoint, uint64_t now, void *context) {
    poll_initial_failure((McuDeliveryExecution *)context, endpoint, now);
    poll_cycle(endpoint, now, context);
    poll_abort((McuDeliveryExecution *)context, endpoint, now);
    poll_postclose_interruption((McuDeliveryExecution *)context, endpoint, now);
    poll_postclose((McuDeliveryExecution *)context, endpoint, now);
    poll_selection((McuDeliveryExecution *)context, endpoint, now);
    poll_continue((McuDeliveryExecution *)context, endpoint, now);
    poll_final((McuDeliveryExecution *)context, endpoint, now);
}

static uint8_t receive(McuControlEndpoint *endpoint, uint8_t message, const uint8_t *payload,
    size_t length, uint64_t received, void *context, McuSessionDecision *decision) {
    McuDeliveryExecution *owner = (McuDeliveryExecution *)context;
    McuSessionCommand command;
    McuOpeningLimits limits;
    ActuatorSnapshot snapshot;
    ActuatorDeliveryCycle cycle;
    McuActuatorEventReservation open_record = {0}, close_record = {0}, interruption_record = {0};
    uint16_t error;
    if (message != ECOBIN_UART_MESSAGE_AUTHORIZE_DELIVERY_FIRST_OPEN) return 0u;
    command.target_boot_id = ecobin_uart_read_u64_be(payload + GRANT(TARGET_MCU_BOOT_ID));
    command.sequence = ecobin_uart_read_u32_be(payload + GRANT(COMMAND_SEQUENCE));
    memcpy(command.uid, payload + GRANT(MCU_COMMAND_UID), sizeof(command.uid));
    memcpy(command.digest, payload + GRANT(COMMAND_DIGEST_SHA256), sizeof(command.digest));
    if (!McuSession_QueryCommand(&endpoint->session, &command, decision)) return 0u;
    if (decision->outcome != ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN) return 1u;
    snapshot = ActuatorRuntime_Snapshot();
    cycle = ActuatorRuntime_DeliveryCycle();
    error = McuOpeningGate_Evaluate(owner->preparation, endpoint, message, payload, length,
        received, snapshot.captured_uptime_ms, &limits);
    if (error == ECOBIN_UART_NACK_ERROR_NONE) {
        if (owner->active || cycle.present || snapshot.lock_powered) error = ECOBIN_UART_NACK_ERROR_BUSY;
        else if (snapshot.update_latched) error = ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
        else if (snapshot.captured_uptime_ms > UINT64_MAX - 100u
            || limits.execution_deadline_ms <= snapshot.captured_uptime_ms + 100u) error = ECOBIN_UART_NACK_ERROR_EXPIRED;
    }
    if (error == ECOBIN_UART_NACK_ERROR_NONE) {
        if (!McuControlEndpoint_ReserveActuatorEvents(endpoint, 1u, &open_record)) error = ECOBIN_UART_NACK_ERROR_BUSY;
        else if (!McuControlEndpoint_ReserveActuatorEvents(endpoint, 1u, &close_record)) {
            McuControlEndpoint_CancelActuatorEvents(endpoint, &open_record);
            error = ECOBIN_UART_NACK_ERROR_BUSY;
        } else if (!McuControlEndpoint_ReserveActuatorEvents(endpoint, 1u, &interruption_record)) {
            McuControlEndpoint_CancelActuatorEvents(endpoint, &open_record);
            McuControlEndpoint_CancelActuatorEvents(endpoint, &close_record);
            error = ECOBIN_UART_NACK_ERROR_BUSY;
        }
    }
    if (!McuSession_ReceiveCommand(&endpoint->session, &command, error, decision)) {
        if (open_record.boot_id) McuControlEndpoint_CancelActuatorEvents(endpoint, &open_record);
        if (close_record.boot_id) McuControlEndpoint_CancelActuatorEvents(endpoint, &close_record);
        if (interruption_record.boot_id) McuControlEndpoint_CancelActuatorEvents(endpoint, &interruption_record);
        return 0u;
    }
    if (!decision->execute_once) {
        if (open_record.boot_id) McuControlEndpoint_CancelActuatorEvents(endpoint, &open_record);
        if (close_record.boot_id) McuControlEndpoint_CancelActuatorEvents(endpoint, &close_record);
        if (interruption_record.boot_id) McuControlEndpoint_CancelActuatorEvents(endpoint, &interruption_record);
        return 1u;
    }
    memcpy(owner->grant, payload, length);
    owner->received_at_ms = received;
    owner->open_record = open_record;
    owner->close_record = close_record;
    owner->interruption_record = interruption_record;
    owner->interruption_reason = owner->interrupted_phase = 0u;
    owner->interrupted_at_ms = 0u;
    owner->active = 1u;
    owner->open_published = owner->close_published = 0u;
    owner->postclose_state = 0u;
    owner->abort_reason = owner->abort_opened = 0u;
    owner->aborted_at_ms = 0u;
    owner->selection_deadline_ms = owner->selected_at_ms = owner->selection_last_now_ms = 0u;
    owner->selection = 0u;
    owner->negative_weight_anomaly = 0u;
    owner->selection_event_sequence = 0u;
    owner->local_cause_sequence = 0u;
    owner->round_index = 1u;
    owner->round_before_grams = owner->preparation->initial.grams;
    memset(&owner->postclose, 0, sizeof(owner->postclose));
    memset(&owner->postclose_meta, 0, sizeof(owner->postclose_meta));
    owner->close_travel_wait_ms = ecobin_uart_read_u32_be(owner->preparation->configuration.active.preimage + DEVICE(DELIVERY_DOOR_TRAVEL_WAIT_MS));
    owner->cycle_token = ActuatorRuntime_BeginDeliveryCycle(limits.execution_deadline_ms, limits.delivery_auto_close_ms);
    owner->rejected_at_ms = ActuatorRuntime_Snapshot().captured_uptime_ms;
    McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COMMAND);
    return 1u;
}

uint8_t McuDeliveryExecution_Attach(McuDeliveryExecution *owner,
    McuWorkPreparation *preparation, McuControlEndpoint *endpoint) {
    if (owner == NULL || !McuWorkPreparation_AttachActions(preparation, endpoint,
        ECOBIN_UART_WORK_TYPE_DELIVERY_SESSION, receive, poll, owner)) return 0u;
    memset(owner, 0, sizeof(*owner));
    owner->preparation = preparation;
    return 1u;
}
