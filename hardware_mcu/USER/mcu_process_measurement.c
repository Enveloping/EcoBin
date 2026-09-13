#include "mcu_process_measurement.h"
#include <string.h>

#define ID_OFFSET(field) (ECOBIN_UART_QUERY_WORK_##field##_OFFSET - ECOBIN_UART_QUERY_WORK_MCU_COMMAND_UID_OFFSET)
#define WEIGHT_OFFSET(field) (ECOBIN_UART_WORK_PREOPEN_WEIGHT_READY_##field##_OFFSET - ECOBIN_UART_WORK_PREOPEN_WEIGHT_READY_MEASUREMENT_KIND_OFFSET)

/* Relative offsets come from the single Registry group, not a second layout. */
static void encode_weight(uint8_t *output, const McuResultMeasurement *measurement,
    const McuProcessMeasurementMeta *meta) {
    output[WEIGHT_OFFSET(MEASUREMENT_KIND)] = measurement->kind;
    ecobin_uart_write_u32_be(output + WEIGHT_OFFSET(REPORTED_WEIGHT_GRAMS), (uint32_t)measurement->grams);
    ecobin_uart_write_u16_be(output + WEIGHT_OFFSET(MEASUREMENT_ELAPSED_MS), measurement->elapsed_ms);
    output[WEIGHT_OFFSET(SAMPLE_COUNT)] = measurement->sample_count;
    ecobin_uart_write_u32_be(output + WEIGHT_OFFSET(SAMPLE_SPAN_GRAMS), measurement->span_grams);
    ecobin_uart_write_u32_be(output + WEIGHT_OFFSET(CALIBRATION_VERSION), measurement->calibration_version);
    ecobin_uart_write_u16_be(output + WEIGHT_OFFSET(FAULT_CODE), measurement->fault_code);
    ecobin_uart_write_u64_be(output + WEIGHT_OFFSET(CONFIG_VERSION), meta->config_version);
}

#define COMMON(message, subject) \
    length = ECOBIN_UART_##message##_PAYLOAD_MAX_LENGTH; \
    if (capacity < length) return 0u; \
    memset(scratch, 0, length); \
    ecobin_uart_write_u64_be(scratch + ECOBIN_UART_##message##_MCU_BOOT_ID_OFFSET, measurement->source_boot_id); \
    ecobin_uart_write_u32_be(scratch + ECOBIN_UART_##message##_MCU_EVENT_SEQUENCE_OFFSET, measurement->event_sequence); \
    ecobin_uart_write_u64_be(scratch + ECOBIN_UART_##message##_UPTIME_MS_OFFSET, meta->observed_uptime_ms); \
    memcpy(scratch + ECOBIN_UART_##message##_##subject##_OFFSET, work->identity + ID_OFFSET(WORK_UID), 16u); \
    scratch[ECOBIN_UART_##message##_PORT_NO_OFFSET] = work->identity[ID_OFFSET(PORT_NO)]; \
    memcpy(scratch + ECOBIN_UART_##message##_MEASUREMENT_UID_OFFSET, measurement->uid, 16u); \
    encode_weight(scratch + ECOBIN_UART_##message##_MEASUREMENT_KIND_OFFSET, measurement, meta)
#define COMMAND(message) \
    memcpy(scratch + ECOBIN_UART_##message##_MCU_COMMAND_UID_OFFSET, work->identity + ID_OFFSET(MCU_COMMAND_UID), 16u)
#define STEP(message, field) \
    ecobin_uart_write_u16_be(scratch + ECOBIN_UART_##message##_##field##_OFFSET, (uint16_t)meta->step_sequence)

size_t McuProcessMeasurement_BuildWorkEvent(const McuWorkState *work,
    const McuResultMeasurement *measurement, const McuProcessMeasurementMeta *meta,
    uint8_t message_type, uint8_t *scratch, size_t capacity) {
    size_t length;
    uint8_t type;
    if (work == NULL || measurement == NULL || meta == NULL || scratch == NULL
        || work->status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING || work->result.held
        || measurement->source_boot_id != work->result.boot_id
        || meta->step_sequence > UINT16_MAX) return 0u;
    type = work->identity[ID_OFFSET(WORK_TYPE)];
    switch (message_type) {
    case ECOBIN_UART_MESSAGE_WORK_PREOPEN_WEIGHT_READY:
        if (type != ECOBIN_UART_WORK_TYPE_DELIVERY_SESSION || meta->step_sequence == 0u
            || work->phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_FIRST_PREOPEN_MEASURING) return 0u;
        COMMON(WORK_PREOPEN_WEIGHT_READY, SESSION_UID);
        COMMAND(WORK_PREOPEN_WEIGHT_READY);
        STEP(WORK_PREOPEN_WEIGHT_READY, ROUND_INDEX);
        break;
    case ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY:
        if (type != ECOBIN_UART_WORK_TYPE_DELIVERY_SESSION || meta->step_sequence == 0u
            || work->phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_POSTCLOSE_MEASURING) return 0u;
        COMMON(WORK_POSTCLOSE_WEIGHT_READY, SESSION_UID);
        COMMAND(WORK_POSTCLOSE_WEIGHT_READY);
        STEP(WORK_POSTCLOSE_WEIGHT_READY, ROUND_INDEX);
        break;
    case ECOBIN_UART_MESSAGE_WORK_PREUNLOCK_WEIGHT_READY:
        if (type != ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION || meta->step_sequence != 0u
            || work->phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_PREUNLOCK_MEASURING) return 0u;
        COMMON(WORK_PREUNLOCK_WEIGHT_READY, OPERATION_UID);
        COMMAND(WORK_PREUNLOCK_WEIGHT_READY);
        break;
    case ECOBIN_UART_MESSAGE_CLEAN_FINAL_WEIGHT_READY:
        if (type != ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION || meta->step_sequence == 0u
            || work->phase != ECOBIN_UART_MCU_WORK_PHASE_CLEAN_FINAL_MEASURING) return 0u;
        COMMON(CLEAN_FINAL_WEIGHT_READY, OPERATION_UID);
        STEP(CLEAN_FINAL_WEIGHT_READY, CLEAN_ACTION_SEQUENCE);
        break;
    default: return 0u;
    }
    if (ecobin_uart_validate_session_payload(message_type, scratch, (uint16_t)length) != 0) return 0u;
    return length;
}

#define FULL(field) (ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_##field##_OFFSET - ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_WORK_FULLNESS_STATUS_OFFSET)
size_t McuProcessMeasurement_BuildWorkEventWithFullness(const McuWorkState *work,
    const McuResultMeasurement *measurement, const McuProcessMeasurementMeta *meta,
    const McuFullnessRun *fullness, uint8_t message_type, uint8_t *scratch, size_t capacity) {
    const McuFullnessResult *result;
    const McuConfigFullnessPolicy *policy;
    uint8_t *out;
    size_t length;
    if (fullness == NULL || !fullness->present || !fullness->result.status
        || (message_type != ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY
            && message_type != ECOBIN_UART_MESSAGE_CLEAN_FINAL_WEIGHT_READY)) return 0u;
    length = McuProcessMeasurement_BuildWorkEvent(work, measurement, meta, message_type, scratch, capacity);
    if (!length) return 0u;
    result = &fullness->result;
    policy = &fullness->policy;
    if (result->config_version != meta->config_version || result->config_version != policy->config_version
        || result->port_no != work->identity[ID_OFFSET(PORT_NO)] || result->port_no != policy->port_no
        || !policy->enabled || result->sensor_kind != policy->sensor_kind || result->requested_count != policy->sample_count) return 0u;
    out = scratch + (message_type == ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY
        ? ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_WORK_FULLNESS_STATUS_OFFSET
        : ECOBIN_UART_CLEAN_FINAL_WEIGHT_READY_WORK_FULLNESS_STATUS_OFFSET);
    out[FULL(WORK_FULLNESS_STATUS)] = result->status;
    ecobin_uart_write_u32_be(out + FULL(FULLNESS_GROUP_SEQUENCE), result->sequence);
    memcpy(out + FULL(FULLNESS_CONFIG_CONTENT_SHA256), result->content_sha256, 32u);
    memcpy(out + FULL(FULLNESS_MCU_PAYLOAD_SHA256), result->mcu_sha256, 32u);
    ecobin_uart_write_u64_be(out + FULL(FULLNESS_STARTED_UPTIME_MS), result->started_ms);
    ecobin_uart_write_u64_be(out + FULL(FULLNESS_COMPLETED_UPTIME_MS), result->completed_ms);
    ecobin_uart_write_u64_be(out + FULL(FULLNESS_LAST_CAPTURED_UPTIME_MS), result->last_captured_ms);
    out[FULL(WORK_FULLNESS_SENSOR_KIND)] = result->sensor_kind;
    out[FULL(WORK_FULLNESS_SENSOR_VALUE)] = result->sensor_value;
    out[FULL(WORK_FULLNESS_BASIS)] = result->basis;
    out[FULL(FULLNESS_DISTANCE_PRESENT)] = result->distance_present;
    ecobin_uart_write_u32_be(out + FULL(FULLNESS_DISTANCE_MM), result->distance_mm);
    out[FULL(FULLNESS_REQUESTED_SAMPLE_COUNT)] = result->requested_count;
    out[FULL(FULLNESS_COMPLETED_SAMPLE_COUNT)] = result->completed_count;
    out[FULL(FULLNESS_VALID_SAMPLE_COUNT)] = result->valid_count;
    out[FULL(FULLNESS_MINIMUM_VALID_SAMPLE_COUNT)] = policy->minimum_valid_count;
    ecobin_uart_write_u32_be(out + FULL(FULLNESS_DISTANCE_THRESHOLD_MM), policy->distance_threshold_mm);
    out[FULL(FULLNESS_STOP_REASON)] = result->stop_reason;
    return ecobin_uart_validate_session_payload(message_type, scratch, (uint16_t)length) == 0 ? length : 0u;
}

#define SCOPE_OFFSET(field) (ECOBIN_UART_QUERY_PROCESS_EVENT_##field##_OFFSET - ECOBIN_UART_QUERY_PROCESS_EVENT_MCU_COMMAND_UID_OFFSET)
size_t McuProcessMeasurement_BuildBaselineEvent(const uint8_t *scope, size_t scope_length,
    const McuResultMeasurement *measurement, const McuProcessMeasurementMeta *meta,
    uint8_t *scratch, size_t capacity) {
    size_t length = ECOBIN_UART_BASELINE_MEASUREMENT_RESULT_PAYLOAD_MAX_LENGTH;
    uint8_t request[ECOBIN_UART_QUERY_PROCESS_EVENT_PAYLOAD_MAX_LENGTH];
    if (scope == NULL || measurement == NULL || meta == NULL || scratch == NULL
        || scope_length != ECOBIN_UART_QUERY_PROCESS_EVENT_PAYLOAD_MAX_LENGTH
            - ECOBIN_UART_QUERY_PROCESS_EVENT_MCU_COMMAND_UID_OFFSET
        || capacity < length || measurement->source_boot_id == 0u || measurement->event_sequence == 0u
        || measurement->source_boot_id != ecobin_uart_read_u64_be(scope + SCOPE_OFFSET(TARGET_MCU_BOOT_ID))
        || scope[SCOPE_OFFSET(WORK_TYPE)] != ECOBIN_UART_WORK_TYPE_BASELINE_MEASUREMENT
        || scope[SCOPE_OFFSET(EVENT_MESSAGE_TYPE)] != ECOBIN_UART_MESSAGE_BASELINE_MEASUREMENT_RESULT
        || ecobin_uart_read_u16_be(scope + SCOPE_OFFSET(STEP_SEQUENCE)) != 0u
        || meta->step_sequence != 0u
        || meta->config_version != ecobin_uart_read_u64_be(scope + SCOPE_OFFSET(CONFIG_VERSION))) return 0u;
    memset(request, 0, ECOBIN_UART_QUERY_PROCESS_EVENT_MCU_COMMAND_UID_OFFSET);
    ecobin_uart_write_u64_be(request, 1u); /* Local shape validation only. */
    memcpy(request + ECOBIN_UART_QUERY_PROCESS_EVENT_MCU_COMMAND_UID_OFFSET, scope, scope_length);
    if (ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_QUERY_PROCESS_EVENT,
        request, sizeof(request)) != 0) return 0u;
    memset(scratch, 0, length);
    ecobin_uart_write_u64_be(scratch + ECOBIN_UART_BASELINE_MEASUREMENT_RESULT_MCU_BOOT_ID_OFFSET,
        measurement->source_boot_id);
    ecobin_uart_write_u32_be(scratch + ECOBIN_UART_BASELINE_MEASUREMENT_RESULT_MCU_EVENT_SEQUENCE_OFFSET,
        measurement->event_sequence);
    ecobin_uart_write_u64_be(scratch + ECOBIN_UART_BASELINE_MEASUREMENT_RESULT_UPTIME_MS_OFFSET,
        meta->observed_uptime_ms);
    memcpy(scratch + ECOBIN_UART_BASELINE_MEASUREMENT_RESULT_MCU_COMMAND_UID_OFFSET,
        scope + SCOPE_OFFSET(MCU_COMMAND_UID), 16u);
    memcpy(scratch + ECOBIN_UART_BASELINE_MEASUREMENT_RESULT_MEASUREMENT_UID_OFFSET,
        scope + SCOPE_OFFSET(WORK_UID), 16u);
    scratch[ECOBIN_UART_BASELINE_MEASUREMENT_RESULT_PORT_NO_OFFSET] = scope[SCOPE_OFFSET(PORT_NO)];
    memcpy(scratch + ECOBIN_UART_BASELINE_MEASUREMENT_RESULT_WEIGHT_MEASUREMENT_UID_OFFSET,
        measurement->uid, 16u);
    encode_weight(scratch + ECOBIN_UART_BASELINE_MEASUREMENT_RESULT_MEASUREMENT_KIND_OFFSET,
        measurement, meta);
    return ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_BASELINE_MEASUREMENT_RESULT,
        scratch, (uint16_t)length) == 0 ? length : 0u;
}
