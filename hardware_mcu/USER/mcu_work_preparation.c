#include "mcu_work_preparation.h"
#include "actuator_runtime.h"
#include <string.h>

typedef char preparation_ram_budget[(sizeof(McuWorkPreparation) <= 3072u) ? 1 : -1];
typedef char start_storage_budget[(ECOBIN_UART_START_CLEAN_OPERATION_PAYLOAD_MAX_LENGTH <= ECOBIN_UART_START_DELIVERY_SESSION_PAYLOAD_MAX_LENGTH
    && ECOBIN_UART_START_DELIVERY_SESSION_PAYLOAD_MAX_LENGTH <= UINT8_MAX) ? 1 : -1];
#define START(field) ECOBIN_UART_START_DELIVERY_SESSION_##field##_OFFSET
#define BASELINE(field) ECOBIN_UART_MEASURE_BASELINE_##field##_OFFSET
#define PROCESS_SCOPE(field) (ECOBIN_UART_QUERY_PROCESS_EVENT_##field##_OFFSET - ECOBIN_UART_QUERY_PROCESS_EVENT_MCU_COMMAND_UID_OFFSET)
#define DEVICE(field) (ECOBIN_UART_CONFIG_PREIMAGE_DEVICE_OFFSET + ECOBIN_UART_CONFIG_DEVICE_BLOCK_##field##_OFFSET - ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET)
#define PORT(field) (ECOBIN_UART_CONFIG_PREIMAGE_PORTS_OFFSET + (policy->port_no - 1u) * ECOBIN_UART_CONFIG_PORT_SEMANTIC_LENGTH + ECOBIN_UART_CONFIG_PORT_BLOCK_##field##_OFFSET - ECOBIN_UART_CONFIG_PORT_BLOCK_PORT_NO_OFFSET)

static uint8_t matching_configuration(const McuWorkPreparation *owner, uint8_t message,
    const uint8_t *payload, const McuConfigWeightPolicy *policy) {
    const uint8_t *preimage = owner->configuration.active.preimage;
    if (ecobin_uart_read_u64_be(payload + START(CONFIG_VERSION)) != policy->config_version
        || memcmp(payload + START(CONFIG_CONTENT_SHA256), preimage + ECOBIN_UART_CONFIG_PREIMAGE_CONTENT_SHA256_OFFSET, 32u) != 0) return 0u;
    if (message == ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION) return 1u;
    return (uint8_t)(memcmp(payload + START(CONTINUE_DELIVERY_WAIT_MS), preimage + DEVICE(CONTINUE_DELIVERY_WAIT_MS), 4u) == 0
        && memcmp(payload + START(NEGATIVE_WEIGHT_THRESHOLD_GRAMS), preimage + DEVICE(NEGATIVE_WEIGHT_THRESHOLD_GRAMS), 4u) == 0
        && memcmp(payload + START(DELIVERY_AUTO_CLOSE_MS), preimage + DEVICE(DELIVERY_AUTO_CLOSE_MS), 4u) == 0
        && memcmp(payload + START(UNIT_PRICE_TEN_THOUSANDTHS), preimage + PORT(UNIT_PRICE_TEN_THOUSANDTHS), 4u) == 0);
}

/* Keep this in lockstep with every fallible McuWeightRun_Begin precondition.
 * A baseline command may be recorded ACCEPTED only when the following Begin
 * is guaranteed to succeed in this single-foreground owner. */
static uint8_t baseline_can_begin(const McuWeightRun *run,
    const McuConfigWeightPolicy *policy, uint8_t port_no, uint64_t now) {
    if (run == NULL || policy == NULL || (run->present && !run->retired) || run->in_flight
        || run->measurement.result.measurement_id == UINT32_MAX || now < run->last_now_ms
        || policy->enabled != 1u || port_no == 0u || port_no > 6u
        || policy->port_no != port_no
        || policy->config_version == 0u || policy->config_version > UINT64_C(9007199254740991)
        || policy->poll_interval_ms == 0u || policy->poll_interval_ms > policy->measurement.timeout_ms
        || policy->response_timeout_ms == 0u || policy->response_timeout_ms > policy->poll_interval_ms)
        return 0u;
    return WeightMeasurement_ConfigValid(&policy->measurement);
}

static void bound(McuControlEndpoint *endpoint, void *context) {
    McuWorkPreparation *owner = (McuWorkPreparation *)context;
    McuConfiguration_Init(&owner->configuration, endpoint->session.boot_id, owner->port_count);
    McuWeightRun_Init(&owner->weight);
    McuFullnessRun_Init(&owner->fullness);
    owner->fullness_measurement_sequence = 0u;
    memset(&owner->initial, 0, sizeof(owner->initial));
    memset(&owner->initial_meta, 0, sizeof(owner->initial_meta));
    memset(&owner->baseline, 0, sizeof(owner->baseline));
    memset(&owner->baseline_meta, 0, sizeof(owner->baseline_meta));
    memset(owner->baseline_scope, 0, sizeof(owner->baseline_scope));
    owner->initial_ready = 0u;
    owner->start_length = 0u;
    owner->start_message = 0u;
    owner->accepted_at_ms = 0u;
    owner->recovery_active = 0u;
    owner->baseline_active = 0u;
    owner->baseline_published = 0u;
    owner->baseline_begin_failed = 0u;
    owner->baseline_first_attempt_sequence = 0u;
}

static uint8_t baseline_command_received(McuWorkPreparation *owner, McuControlEndpoint *endpoint,
    const uint8_t *payload, size_t length, uint64_t now, McuSessionDecision *decision) {
    McuSessionCommand command;
    McuConfigWeightPolicy policy;
    uint16_t error;
    uint32_t measurement_sequence;
    command.target_boot_id = ecobin_uart_read_u64_be(payload + BASELINE(TARGET_MCU_BOOT_ID));
    command.sequence = ecobin_uart_read_u32_be(payload + BASELINE(COMMAND_SEQUENCE));
    memcpy(command.uid, payload + BASELINE(MCU_COMMAND_UID), sizeof(command.uid));
    memcpy(command.digest, payload + BASELINE(COMMAND_DIGEST_SHA256), sizeof(command.digest));
    if (!McuSession_QueryCommand(&endpoint->session, &command, decision)) return 0u;
    if (decision->outcome != ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN) return 1u;
    error = owner->guard(ECOBIN_UART_MESSAGE_MEASURE_BASELINE, payload, length, now, owner->guard_context);
    if (error > ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT) error = ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT;
    if (error == ECOBIN_UART_NACK_ERROR_NONE) {
        if (owner->recovery_active || owner->baseline_active
            || (owner->weight.present && !owner->weight.retired) || owner->weight.in_flight
            || endpoint->work.status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING || endpoint->work.result.held
            || endpoint->process_event.held || McuConfiguration_IsStaging(&owner->configuration))
            error = ECOBIN_UART_NACK_ERROR_BUSY;
        else if (payload[BASELINE(PORT_NO)] != endpoint->facts.port_no
            || owner->configuration.active.boot_id != endpoint->session.boot_id
            || !McuConfiguration_ReadWeightPolicy(&owner->configuration, payload[BASELINE(PORT_NO)], &policy)
            || endpoint->facts.config_staging || endpoint->facts.config_version != policy.config_version
            || ecobin_uart_read_u64_be(payload + BASELINE(CONFIG_VERSION)) != policy.config_version
            || memcmp(payload + BASELINE(CONFIG_CONTENT_SHA256),
                owner->configuration.active.preimage + ECOBIN_UART_CONFIG_PREIMAGE_CONTENT_SHA256_OFFSET, 32u) != 0
            || memcmp(endpoint->facts.content_sha256,
                owner->configuration.active.preimage + ECOBIN_UART_CONFIG_PREIMAGE_CONTENT_SHA256_OFFSET, 32u) != 0
            || memcmp(endpoint->facts.mcu_sha256, owner->configuration.active.expected_digest, 32u) != 0
            || !baseline_can_begin(&owner->weight, &policy, payload[BASELINE(PORT_NO)], now)
            || ecobin_uart_read_u32_be(payload + BASELINE(MEASUREMENT_TIMEOUT_MS)) != policy.measurement.timeout_ms
            || UINT32_MAX - endpoint->critical_event_sequence
                <= McuActuatorEventJournal_PendingCount(&endpoint->actuator_events))
            error = ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    }
    if (!McuSession_ReceiveCommand(&endpoint->session, &command, error, decision)) return 0u;
    if (!decision->execute_once) return 1u;
    memset(&owner->baseline, 0, sizeof(owner->baseline));
    memset(&owner->baseline_meta, 0, sizeof(owner->baseline_meta));
    memset(owner->baseline_scope, 0, sizeof(owner->baseline_scope));
    memcpy(owner->baseline_scope + PROCESS_SCOPE(MCU_COMMAND_UID), command.uid, sizeof(command.uid));
    memcpy(owner->baseline_scope + PROCESS_SCOPE(COMMAND_DIGEST_SHA256), command.digest, sizeof(command.digest));
    ecobin_uart_write_u64_be(owner->baseline_scope + PROCESS_SCOPE(TARGET_MCU_BOOT_ID), command.target_boot_id);
    ecobin_uart_write_u32_be(owner->baseline_scope + PROCESS_SCOPE(COMMAND_SEQUENCE), command.sequence);
    memcpy(owner->baseline_scope + PROCESS_SCOPE(WORK_UID), payload + BASELINE(MEASUREMENT_UID), 16u);
    owner->baseline_scope[PROCESS_SCOPE(WORK_TYPE)] = ECOBIN_UART_WORK_TYPE_BASELINE_MEASUREMENT;
    owner->baseline_scope[PROCESS_SCOPE(PORT_NO)] = payload[BASELINE(PORT_NO)];
    owner->baseline_scope[PROCESS_SCOPE(EVENT_MESSAGE_TYPE)] = ECOBIN_UART_MESSAGE_BASELINE_MEASUREMENT_RESULT;
    ecobin_uart_write_u64_be(owner->baseline_scope + PROCESS_SCOPE(CONFIG_VERSION), policy.config_version);
    owner->baseline_first_attempt_sequence = owner->weight.attempt_sequence;
    measurement_sequence = owner->weight.measurement.result.measurement_id + 1u;
    owner->baseline_active = 1u;
    owner->baseline_published = 0u;
    owner->baseline_begin_failed = 0u;
    owner->baseline_meta.config_version = policy.config_version;
    owner->baseline_meta.observed_uptime_ms = now;
    if (!McuWeightRun_Begin(&owner->weight, &policy, measurement_sequence, now)) {
        /* Preflight above makes this unreachable without an internal state or
         * implementation defect. Do not leave an accepted command resultless:
         * publish an explicit non-value CONFIG_ERROR through the normal
         * retained event slot and expose a local diagnostic. */
        endpoint->parser.diagnostics |= ECOBIN_UART_DIAG_SEMANTIC_REJECTED;
        owner->baseline_begin_failed = 1u;
        owner->baseline.kind = ECOBIN_UART_RESULT_MEASUREMENT_KIND_CONFIG_ERROR;
        owner->baseline.calibration_version = policy.calibration_version;
        owner->baseline.fault_code = ECOBIN_UART_FAULT_CODE_WEIGHT_CONFIG;
    }
    return 1u;
}

static uint8_t command_received(McuControlEndpoint *endpoint, uint8_t message,
    const uint8_t *payload, size_t length, uint64_t now, void *context, McuSessionDecision *decision) {
    McuWorkPreparation *owner = (McuWorkPreparation *)context;
    McuSessionCommand command;
    McuConfigWeightPolicy policy;
    uint8_t identity[MCU_WORK_IDENTITY_LENGTH];
    uint8_t phase, action, handled;
    uint16_t error;
    uint32_t measurement_sequence;
    if (message == ECOBIN_UART_MESSAGE_MEASURE_BASELINE)
        return baseline_command_received(owner, endpoint, payload, length, now, decision);
    if (message != ECOBIN_UART_MESSAGE_START_DELIVERY_SESSION && message != ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION
        && (message < ECOBIN_UART_MESSAGE_CONFIG_BEGIN || message > ECOBIN_UART_MESSAGE_CONFIG_COMMIT)) {
        if (message == ECOBIN_UART_MESSAGE_SAFE_CLOSE)
            return owner->recovery.handler != NULL && owner->recovery.handler(endpoint,
                message, payload, length, now, owner->recovery.context, decision);
        for (action = 0u; action < 2u; ++action)
            if (owner->actions[action].handler != NULL && owner->actions[action].handler(endpoint,
                message, payload, length, now, owner->actions[action].context, decision)) return 1u;
        if (message == ECOBIN_UART_MESSAGE_AUTHORIZE_DELIVERY_FIRST_OPEN || message == ECOBIN_UART_MESSAGE_UNLOCK_CLEAN_DOOR) {
            command.target_boot_id = ecobin_uart_read_u64_be(payload + START(TARGET_MCU_BOOT_ID));
            command.sequence = ecobin_uart_read_u32_be(payload + START(COMMAND_SEQUENCE));
            memcpy(command.uid, payload + START(MCU_COMMAND_UID), sizeof(command.uid));
            memcpy(command.digest, payload + START(COMMAND_DIGEST_SHA256), sizeof(command.digest));
            return McuSession_ReceiveCommand(&endpoint->session, &command,
                ECOBIN_UART_NACK_ERROR_UNSUPPORTED_MESSAGE, decision);
        }
        return 0u;
    }
    command.target_boot_id = ecobin_uart_read_u64_be(payload + START(TARGET_MCU_BOOT_ID));
    command.sequence = ecobin_uart_read_u32_be(payload + START(COMMAND_SEQUENCE));
    memcpy(command.uid, payload + START(MCU_COMMAND_UID), sizeof(command.uid));
    memcpy(command.digest, payload + START(COMMAND_DIGEST_SHA256), sizeof(command.digest));
    if (!McuSession_QueryCommand(&endpoint->session, &command, decision)) return 0u;
    if (decision->outcome != ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN) return 1u;
    error = owner->guard(message, payload, length, now, owner->guard_context);
    if (error > ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT) error = ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT;
    if (error == ECOBIN_UART_NACK_ERROR_NONE && (owner->recovery_active || owner->baseline_active
        || (owner->weight.present && !owner->weight.retired) || owner->weight.in_flight))
        error = ECOBIN_UART_NACK_ERROR_BUSY;
    if (message >= ECOBIN_UART_MESSAGE_CONFIG_BEGIN && message <= ECOBIN_UART_MESSAGE_CONFIG_COMMIT) {
        handled = McuConfiguration_Receive(&owner->configuration, &endpoint->session, &endpoint->work,
            error, message, payload, length, decision);
        if (handled && message == ECOBIN_UART_MESSAGE_CONFIG_COMMIT && decision->execute_once
            && owner->apply_configuration != NULL
            && owner->apply_configuration(&owner->configuration, owner->configuration_context))
            McuDeviceFacts_PublishConfiguration(&endpoint->facts,
                ecobin_uart_read_u64_be(payload + ECOBIN_UART_CONFIG_COMMIT_CONFIG_VERSION_OFFSET),
                owner->configuration.active.preimage + ECOBIN_UART_CONFIG_PREIMAGE_CONTENT_SHA256_OFFSET,
                owner->configuration.active.expected_digest, 0u);
        return handled;
    }
    memcpy(identity, payload, START(PORT_NO));
    identity[START(PORT_NO)] = message == ECOBIN_UART_MESSAGE_START_DELIVERY_SESSION
        ? ECOBIN_UART_WORK_TYPE_DELIVERY_SESSION : ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION;
    identity[START(PORT_NO) + 1u] = payload[START(PORT_NO)];
    phase = message == ECOBIN_UART_MESSAGE_START_DELIVERY_SESSION
        ? ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_FIRST_PREOPEN_MEASURING : ECOBIN_UART_MCU_WORK_PHASE_CLEAN_PREUNLOCK_MEASURING;
    if (error == ECOBIN_UART_NACK_ERROR_NONE) {
        if (McuConfiguration_IsStaging(&owner->configuration)
            || endpoint->work.status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING || endpoint->work.result.held)
            error = ECOBIN_UART_NACK_ERROR_BUSY;
        else if (payload[START(PORT_NO)] != endpoint->facts.port_no
            || endpoint->work.result.boot_id != endpoint->session.boot_id
            || owner->configuration.active.boot_id != endpoint->session.boot_id
            || !McuConfiguration_ReadWeightPolicy(&owner->configuration, payload[START(PORT_NO)], &policy)
            || endpoint->facts.config_staging || endpoint->facts.config_version != policy.config_version
            || memcmp(endpoint->facts.content_sha256, owner->configuration.active.preimage + ECOBIN_UART_CONFIG_PREIMAGE_CONTENT_SHA256_OFFSET, 32u) != 0
            || memcmp(endpoint->facts.mcu_sha256, owner->configuration.active.expected_digest, 32u) != 0
            || !policy.enabled || !matching_configuration(owner, message, payload, &policy)
            || !McuWorkState_CanBegin(&endpoint->work, identity, sizeof(identity), phase)
            || now < owner->weight.last_now_ms || owner->weight.measurement.result.measurement_id == UINT32_MAX)
            error = ECOBIN_UART_NACK_ERROR_STATE_CONFLICT;
    }
    if (!McuSession_ReceiveCommand(&endpoint->session, &command, error, decision)) return 0u;
    if (decision->execute_once) {
        memcpy(owner->start_payload, payload, length);
        owner->start_length = (uint8_t)length;
        owner->start_message = message;
        owner->accepted_at_ms = now;
        memset(&owner->initial, 0, sizeof(owner->initial));
        memset(&owner->initial_meta, 0, sizeof(owner->initial_meta));
        owner->initial_ready = 0u;
        measurement_sequence = owner->weight.measurement.result.measurement_id + 1u;
        if (!McuWorkState_BeginAccepted(&endpoint->work, identity, sizeof(identity), phase)
            || !McuWeightRun_Begin(&owner->weight, &policy, measurement_sequence, now))
            McuWorkState_SetPhase(&endpoint->work, ECOBIN_UART_MCU_WORK_PHASE_SAFETY_LOCKED);
    }
    return 1u;
}

static void baseline_failure(McuResultMeasurement *measurement, const ScaleReaderObservation *observation,
    uint8_t has_observation) {
    measurement->kind = ECOBIN_UART_RESULT_MEASUREMENT_KIND_UNAVAILABLE;
    measurement->fault_code = ECOBIN_UART_FAULT_CODE_WEIGHT_TIMEOUT;
    if (!has_observation) return;
    switch (observation->status) {
    case SCALE_READER_RANGE_ERROR:
        measurement->kind = ECOBIN_UART_RESULT_MEASUREMENT_KIND_OVERLOAD;
        measurement->fault_code = ECOBIN_UART_FAULT_CODE_WEIGHT_OVERLOAD;
        break;
    case SCALE_READER_CRC_ERROR:
    case SCALE_READER_PROTOCOL_ERROR:
        measurement->kind = ECOBIN_UART_RESULT_MEASUREMENT_KIND_PROTOCOL_ERROR;
        measurement->fault_code = ECOBIN_UART_FAULT_CODE_WEIGHT_PROTOCOL;
        break;
    case SCALE_READER_TIMEOUT:
        measurement->kind = ECOBIN_UART_RESULT_MEASUREMENT_KIND_DISCONNECTED;
        measurement->fault_code = ECOBIN_UART_FAULT_CODE_WEIGHT_DISCONNECTED;
        break;
    default: break;
    }
}

static uint8_t poll_baseline(McuWorkPreparation *owner, McuControlEndpoint *endpoint, uint64_t now) {
    WeightMeasurementResult result;
    McuConfigWeightPolicy policy;
    ScaleReaderObservation observation;
    uint8_t uid[16], has_observation;
    uint32_t event_sequence;
    uint64_t observed;
    size_t length;
    if (owner->baseline_published) {
        if (endpoint->process_event.held) return 1u;
        owner->baseline_active = owner->baseline_published = 0u;
        owner->baseline_begin_failed = 0u;
        return 1u;
    }
    has_observation = 0u;
    observed = owner->baseline_meta.observed_uptime_ms;
    memset(&result, 0, sizeof(result));
    memset(&policy, 0, sizeof(policy));
    if (!owner->baseline_begin_failed) {
        if (!McuWeightRun_Poll(&owner->weight, now)
            || !McuWeightRun_Copy(&owner->weight, &result, &policy)) return 0u;
        has_observation = McuWeightRun_CopyObservation(&owner->weight, &observation)
            && observation.attempt_sequence > owner->baseline_first_attempt_sequence
            && observation.captured_ms >= owner->weight.started_ms;
        if (has_observation && !McuDeviceFacts_PublishScaleObservation(&endpoint->facts, &observation)) return 0u;
        observed = result.status == WEIGHT_MEASUREMENT_PENDING ? now : owner->weight.started_ms + result.elapsed_ms;
        if (!McuDeviceFacts_PublishMeasurement(&endpoint->facts, &result, policy.config_version, observed)) return 0u;
        if (result.status == WEIGHT_MEASUREMENT_PENDING) return 1u;
        if (result.status != WEIGHT_MEASUREMENT_STABLE_MEAN && result.status != WEIGHT_MEASUREMENT_TIMEOUT_MEDIAN
            && result.status != WEIGHT_MEASUREMENT_UNAVAILABLE) return 0u;
    }
    event_sequence = McuControlEndpoint_ReserveEventSequence(endpoint);
    if (!event_sequence) return 0u;
    memcpy(uid, "EBM1", 4u);
    ecobin_uart_write_u64_be(uid + 4u, endpoint->session.boot_id);
    ecobin_uart_write_u32_be(uid + 12u, event_sequence);
    if (owner->baseline_begin_failed) {
        memcpy(owner->baseline.uid, uid, sizeof(uid));
        owner->baseline.source_boot_id = endpoint->session.boot_id;
        owner->baseline.event_sequence = event_sequence;
    } else if (!McuResultMeasurement_FromAvailable(&owner->baseline, &result, uid,
            endpoint->session.boot_id, event_sequence, policy.calibration_version)) {
        memset(&owner->baseline, 0, sizeof(owner->baseline));
        memcpy(owner->baseline.uid, uid, sizeof(uid));
        owner->baseline.source_boot_id = endpoint->session.boot_id;
        owner->baseline.event_sequence = event_sequence;
        owner->baseline.elapsed_ms = (uint16_t)result.elapsed_ms;
        owner->baseline.sample_count = result.sample_count;
        owner->baseline.calibration_version = policy.calibration_version;
        baseline_failure(&owner->baseline, &observation, has_observation);
    }
    if (!owner->baseline_begin_failed)
        owner->baseline_meta.config_version = policy.config_version;
    owner->baseline_meta.observed_uptime_ms = observed;
    length = McuProcessMeasurement_BuildBaselineEvent(owner->baseline_scope,
        sizeof(owner->baseline_scope), &owner->baseline, &owner->baseline_meta,
        owner->scratch, sizeof(owner->scratch));
    if (!length || !McuProcessEventSlot_Freeze(&endpoint->process_event, owner->baseline_scope,
        sizeof(owner->baseline_scope), ECOBIN_UART_MESSAGE_BASELINE_MEASUREMENT_RESULT,
        owner->scratch, length)) return 0u;
    if (!owner->baseline_begin_failed
        && !McuWeightRun_Retire(&owner->weight, result.measurement_id)) return 0u;
    owner->baseline_published = 1u;
    return 1u;
}

uint8_t McuWorkPreparation_Attach(McuWorkPreparation *owner, McuControlEndpoint *endpoint,
    uint8_t port_count, McuPreparationGuard guard, void *context) {
    if (owner == NULL || guard == NULL || port_count == 0u || port_count > 6u
        || !McuControlEndpoint_AttachCommands(endpoint, command_received, bound, owner)) return 0u;
    memset(owner, 0, sizeof(*owner));
    owner->guard = guard;
    owner->guard_context = context;
    owner->port_count = port_count;
    McuConfiguration_Init(&owner->configuration, 0u, port_count);
    McuWeightRun_Init(&owner->weight);
    return 1u;
}

uint8_t McuWorkPreparation_AttachRecovery(McuWorkPreparation *owner, McuControlEndpoint *endpoint,
    McuControlCommandHandler handler, McuPreparedActionPoll poll, void *context) {
    if (owner == NULL || endpoint == NULL || handler == NULL || poll == NULL || context == NULL
        || endpoint->application_context != owner || endpoint->session.boot_id != 0u || endpoint->feeding
        || owner->recovery.handler != NULL || owner->actions[0].context == context || owner->actions[1].context == context) return 0u;
    owner->recovery.handler = handler;
    owner->recovery.poll = poll;
    owner->recovery.context = context;
    return 1u;
}

uint8_t McuWorkPreparation_SetConfigurationApply(McuWorkPreparation *owner, McuControlEndpoint *endpoint,
    McuPreparationApplyConfiguration apply, void *context) {
    if (owner == NULL || endpoint == NULL || apply == NULL || endpoint->application_context != owner
        || endpoint->session.boot_id != 0u || endpoint->feeding || owner->apply_configuration != NULL) return 0u;
    owner->apply_configuration = apply;
    owner->configuration_context = context;
    return 1u;
}

uint8_t McuWorkPreparation_AttachFullness(McuWorkPreparation *owner, McuControlEndpoint *endpoint) {
    if (owner == NULL || endpoint == NULL || endpoint->application_context != owner
        || endpoint->session.boot_id != 0u || endpoint->feeding || owner->fullness_enabled
        || !UltrasonicReader_Claim(&owner->fullness)) return 0u;
    if (!UltrasonicReader_Release(&owner->fullness)) return 0u;
    owner->fullness_enabled = 1u;
    return 1u;
}

size_t McuWorkPreparation_CopyStart(const McuWorkPreparation *owner, uint8_t *output,
    size_t capacity, uint8_t *message, uint64_t *accepted_at_ms) {
    if (owner == NULL || output == NULL || message == NULL || accepted_at_ms == NULL
        || owner->start_length == 0u || capacity < owner->start_length) return 0u;
    memcpy(output, owner->start_payload, owner->start_length);
    *message = owner->start_message;
    *accepted_at_ms = owner->accepted_at_ms;
    return owner->start_length;
}

uint8_t McuWorkPreparation_AttachActions(McuWorkPreparation *owner, McuControlEndpoint *endpoint,
    uint8_t work_type, McuControlCommandHandler handler, McuPreparedActionPoll poll, void *context) {
    uint8_t index = work_type == ECOBIN_UART_WORK_TYPE_DELIVERY_SESSION ? 0u : 1u;
    if (owner == NULL || endpoint == NULL || handler == NULL || poll == NULL || context == NULL
        || (work_type != ECOBIN_UART_WORK_TYPE_DELIVERY_SESSION && work_type != ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION)
        || endpoint->application_context != owner || endpoint->session.boot_id != 0u || endpoint->feeding
        || owner->actions[index].handler != NULL || owner->actions[1u - index].context == context
        || owner->recovery.context == context) return 0u;
    owner->actions[index].handler = handler;
    owner->actions[index].poll = poll;
    owner->actions[index].context = context;
    return 1u;
}

uint8_t McuWorkPreparation_InterruptMeasurement(McuWorkPreparation *owner, uint64_t now) {
    uint32_t sequence;
    if (owner == NULL) return 0u;
    sequence = owner->weight.measurement.result.measurement_id;
    if (!McuWeightRun_Interrupt(&owner->weight, sequence, now)) return 0u;
    /* Weight may already be terminal, so its status alone cannot signal a
     * business interruption to the independently pending sensor group. */
    if (owner->fullness.present) McuFullnessRun_Interrupt(&owner->fullness);
    else owner->fullness_measurement_sequence = sequence;
    return 1u; /* Optional-source cleanup never blocks the business fault exit. */
}

uint8_t McuWorkPreparation_PollMeasurement(McuWorkPreparation *owner, McuControlEndpoint *endpoint,
    uint64_t now, uint8_t message, uint16_t step, McuResultMeasurement *measurement, McuProcessMeasurementMeta *meta) {
    WeightMeasurementResult result;
    McuConfigWeightPolicy policy;
    ScaleReaderObservation observation;
    uint8_t uid[16], scope[ECOBIN_UART_QUERY_PROCESS_EVENT_PAYLOAD_MAX_LENGTH - 8u];
    uint8_t expected_phase, expected_type, fullness_ready = 1u;
    uint32_t event_sequence;
    uint64_t observed;
    size_t length;
    if (owner == NULL || endpoint == NULL || measurement == NULL || meta == NULL
        || endpoint->feeding || endpoint->application_context != owner || now < endpoint->last_input_ms
        || endpoint->work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING || endpoint->work.result.held) return 0u;
    expected_type = ECOBIN_UART_WORK_TYPE_DELIVERY_SESSION;
    switch (message) {
    case ECOBIN_UART_MESSAGE_WORK_PREOPEN_WEIGHT_READY:
        expected_phase = ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_FIRST_PREOPEN_MEASURING; break;
    case ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY:
        expected_phase = ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_POSTCLOSE_MEASURING; break;
    case ECOBIN_UART_MESSAGE_WORK_PREUNLOCK_WEIGHT_READY:
        expected_phase = ECOBIN_UART_MCU_WORK_PHASE_CLEAN_PREUNLOCK_MEASURING;
        expected_type = ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION; break;
    case ECOBIN_UART_MESSAGE_CLEAN_FINAL_WEIGHT_READY:
        expected_phase = ECOBIN_UART_MCU_WORK_PHASE_CLEAN_FINAL_MEASURING;
        expected_type = ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION; break;
    default: return 0u;
    }
    if (endpoint->work.phase != expected_phase || endpoint->work.identity[MCU_WORK_IDENTITY_LENGTH - 2u] != expected_type
        || (message == ECOBIN_UART_MESSAGE_WORK_PREUNLOCK_WEIGHT_READY ? step != 0u : step == 0u)) return 0u;
    if (!McuWeightRun_Poll(&owner->weight, now) || !McuWeightRun_Copy(&owner->weight, &result, &policy)) return 0u;
    if (McuWeightRun_CopyObservation(&owner->weight, &observation)
        && !McuDeviceFacts_PublishScaleObservation(&endpoint->facts, &observation)) return 0u;
    /* Late foreground publication must not refresh a historical terminal value.
     * The core elapsed time is frozen at resolution / acquisition deadline. */
    observed = result.status == WEIGHT_MEASUREMENT_PENDING ? now : owner->weight.started_ms + result.elapsed_ms;
    if (!McuDeviceFacts_PublishMeasurement(&endpoint->facts, &result, policy.config_version, observed)) return 0u;
    if (owner->fullness_enabled && (message == ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY
        || message == ECOBIN_UART_MESSAGE_CLEAN_FINAL_WEIGHT_READY)) {
        if (owner->fullness_measurement_sequence != result.measurement_id) {
            if (owner->fullness.present && McuFullnessRun_Interrupt(&owner->fullness))
                McuFullnessRun_Retire(&owner->fullness, owner->fullness.result.sequence);
            /* One attempt to establish this phase's group. Unsupported or
             * unavailable source is explicitly NOT_SAMPLED, not a CLEAR result.
             * Never retry into a different phase or start sampling after stop. */
            if (!owner->fullness.present) {
                owner->fullness_measurement_sequence = result.measurement_id;
                if (result.status != WEIGHT_MEASUREMENT_INTERRUPTED && !ActuatorRuntime_Snapshot().update_latched)
                    McuFullnessRun_Begin(&owner->fullness, &endpoint->facts, &owner->configuration);
            }
        }
        if (owner->fullness.present && owner->fullness_measurement_sequence == result.measurement_id)
            fullness_ready = result.status == WEIGHT_MEASUREMENT_INTERRUPTED || ActuatorRuntime_Snapshot().update_latched
                ? McuFullnessRun_Interrupt(&owner->fullness) : McuFullnessRun_Poll(&owner->fullness);
    }
    if (result.status == WEIGHT_MEASUREMENT_PENDING) return 1u;
    /* Auxiliary sampling is best effort. It cannot extend a completed weight
     * acquisition or keep this business waiting for a broken optional source. */
    if (!fullness_ready && owner->fullness.present)
        McuFullnessRun_Interrupt(&owner->fullness);
    if (result.status != WEIGHT_MEASUREMENT_STABLE_MEAN && result.status != WEIGHT_MEASUREMENT_TIMEOUT_MEDIAN
        && result.status != WEIGHT_MEASUREMENT_UNAVAILABLE && result.status != WEIGHT_MEASUREMENT_INTERRUPTED) return 0u;
    if (measurement->event_sequence == 0u) {
        event_sequence = McuControlEndpoint_ReserveEventSequence(endpoint);
        if (event_sequence == 0u) return 0u;
        /* Non-random 128-bit measurement identity, unique within the durable
         * Pi-assigned boot and shared critical event sequence. Not txSequence. */
        memcpy(uid, "EBM1", 4u);
        ecobin_uart_write_u64_be(uid + 4u, endpoint->session.boot_id);
        ecobin_uart_write_u32_be(uid + 12u, event_sequence);
        if (result.status == WEIGHT_MEASUREMENT_UNAVAILABLE || result.status == WEIGHT_MEASUREMENT_INTERRUPTED) {
            measurement->kind = result.status == WEIGHT_MEASUREMENT_INTERRUPTED
                ? ECOBIN_UART_RESULT_MEASUREMENT_KIND_INTERRUPTED : ECOBIN_UART_RESULT_MEASUREMENT_KIND_UNAVAILABLE;
            memcpy(measurement->uid, uid, sizeof(uid));
            measurement->source_boot_id = endpoint->session.boot_id;
            measurement->event_sequence = event_sequence;
            measurement->elapsed_ms = (uint16_t)result.elapsed_ms;
            measurement->sample_count = result.sample_count;
            measurement->calibration_version = policy.calibration_version;
            measurement->fault_code = result.status == WEIGHT_MEASUREMENT_INTERRUPTED
                ? ECOBIN_UART_FAULT_CODE_MEASUREMENT_INTERRUPTED : ECOBIN_UART_FAULT_CODE_WEIGHT_TIMEOUT;
        } else if (!McuResultMeasurement_FromAvailable(measurement, &result, uid,
            endpoint->session.boot_id, event_sequence, policy.calibration_version)) return 0u;
        meta->config_version = policy.config_version;
        meta->observed_uptime_ms = observed;
        meta->step_sequence = step;
    }
    length = owner->fullness.present && owner->fullness_measurement_sequence == result.measurement_id
        ? McuProcessMeasurement_BuildWorkEventWithFullness(&endpoint->work, measurement, meta,
            &owner->fullness, message, owner->scratch, sizeof(owner->scratch))
        : McuProcessMeasurement_BuildWorkEvent(&endpoint->work, measurement, meta,
            message, owner->scratch, sizeof(owner->scratch));
    memcpy(scope, endpoint->work.identity, MCU_WORK_IDENTITY_LENGTH);
    scope[ECOBIN_UART_QUERY_PROCESS_EVENT_EVENT_MESSAGE_TYPE_OFFSET - 8u] = message;
    ecobin_uart_write_u16_be(scope + ECOBIN_UART_QUERY_PROCESS_EVENT_STEP_SEQUENCE_OFFSET - 8u, step);
    ecobin_uart_write_u64_be(scope + ECOBIN_UART_QUERY_PROCESS_EVENT_CONFIG_VERSION_OFFSET - 8u, policy.config_version);
    /* Diagnostic mailbox only: retain an existing unacknowledged event, or
     * offer this event if empty. Neither loss nor SAVED controls local work.
     * The authoritative first/final measurements live in the executor and
     * subsequently in WORK_RESULT, retained until its own durable ACK. */
    if (length && !endpoint->process_event.held)
        McuProcessEventSlot_Freeze(&endpoint->process_event, scope, sizeof(scope),
            message, owner->scratch, length);
    McuWeightRun_Retire(&owner->weight, result.measurement_id);
    if (owner->fullness.present) McuFullnessRun_Retire(&owner->fullness, owner->fullness.result.sequence);
    return 2u;
}

uint8_t McuWorkPreparation_Poll(McuWorkPreparation *owner, McuControlEndpoint *endpoint, uint64_t now) {
    uint8_t clean, result, available, action;
    if (owner == NULL || endpoint == NULL || endpoint->feeding || endpoint->application_context != owner) return 0u;
    if (owner->recovery.poll != NULL) owner->recovery.poll(endpoint, now, owner->recovery.context);
    for (action = 0u; action < 2u; ++action)
        if (owner->actions[action].poll != NULL)
            owner->actions[action].poll(endpoint, now, owner->actions[action].context);
    if (owner->baseline_active) return poll_baseline(owner, endpoint, now);
    if (owner->initial_ready) return 1u;
    clean = endpoint->work.identity[MCU_WORK_IDENTITY_LENGTH - 2u] == ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION;
    result = McuWorkPreparation_PollMeasurement(owner, endpoint, now,
        clean ? ECOBIN_UART_MESSAGE_WORK_PREUNLOCK_WEIGHT_READY : ECOBIN_UART_MESSAGE_WORK_PREOPEN_WEIGHT_READY,
        clean ? 0u : 1u, &owner->initial, &owner->initial_meta);
    if (result != 2u) return result;
    owner->initial_ready = 1u;
    available = owner->initial.kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_STABLE_MEAN
        || owner->initial.kind == ECOBIN_UART_RESULT_MEASUREMENT_KIND_TIMEOUT_MEDIAN;
    McuWorkState_SetPhase(&endpoint->work, available
        ? (clean ? ECOBIN_UART_MCU_WORK_PHASE_CLEAN_WAIT_FIRST_UNLOCK : ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_FIRST_OPEN_AUTH)
        : (clean ? ECOBIN_UART_MCU_WORK_PHASE_CLEAN_FINALIZING : ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_FINALIZING));
    return 1u;
}
