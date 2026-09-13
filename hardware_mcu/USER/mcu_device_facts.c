#include "mcu_device_facts.h"
#include "actuator_runtime.h"
#include "scale_reader.h"
#include <string.h>

typedef char facts_ram_budget[(sizeof(McuDeviceFacts) <= 224u) ? 1 : -1];
#define OFFSET(field) ECOBIN_UART_DEVICE_FACTS_REPLY_##field##_OFFSET
#define U8(field, value) output[OFFSET(field)] = (uint8_t)(value)
#define U16(field, value) ecobin_uart_write_u16_be(output + OFFSET(field), (uint16_t)(value))
#define U32(field, value) ecobin_uart_write_u32_be(output + OFFSET(field), (uint32_t)(value))
#define U64(field, value) ecobin_uart_write_u64_be(output + OFFSET(field), (uint64_t)(value))
#define BYTES(field, data, length) memcpy(output + OFFSET(field), data, length)
#define WORK_OFFSET(field) (ECOBIN_UART_QUERY_WORK_##field##_OFFSET - ECOBIN_UART_QUERY_WORK_MCU_COMMAND_UID_OFFSET)

void McuDeviceFacts_Init(McuDeviceFacts *facts, uint8_t port_no) {
    memset(facts, 0, sizeof(*facts));
    if (port_no >= 1u && port_no <= 6u) facts->port_no = port_no;
}

uint8_t McuDeviceFacts_PublishConfiguration(McuDeviceFacts *facts, uint64_t version,
    const uint8_t *content_sha256, const uint8_t *mcu_sha256, uint8_t staging) {
    int content_zero, mcu_zero;
    if (content_sha256 == NULL || mcu_sha256 == NULL || staging > 1u
        || version > UINT64_C(9007199254740991) || version < facts->config_version) return 0u;
    content_zero = ecobin_uart_bytes_zero(content_sha256, 32u);
    mcu_zero = ecobin_uart_bytes_zero(mcu_sha256, 32u);
    if (version == 0u ? (!content_zero || !mcu_zero) : (content_zero || mcu_zero)) return 0u;
    if (version == facts->config_version && (memcmp(content_sha256, facts->content_sha256, 32u) != 0
        || memcmp(mcu_sha256, facts->mcu_sha256, 32u) != 0)) return 0u;
    facts->config_version = version;
    memcpy(facts->content_sha256, content_sha256, 32u);
    memcpy(facts->mcu_sha256, mcu_sha256, 32u);
    facts->config_staging = staging;
    return 1u;
}

static uint8_t same_measurement(const WeightMeasurementResult *a, const WeightMeasurementResult *b) {
    return (uint8_t)(a->measurement_id == b->measurement_id && a->elapsed_ms == b->elapsed_ms
        && a->sample_span_grams == b->sample_span_grams && a->grams == b->grams
        && a->status == b->status && a->value_available == b->value_available && a->sample_count == b->sample_count);
}

uint8_t McuDeviceFacts_PublishMeasurement(McuDeviceFacts *facts, const WeightMeasurementResult *result,
    uint64_t config_version, uint64_t observed_ms) {
    ActuatorSnapshot now = ActuatorRuntime_Snapshot();
    uint8_t success;
    if (result == NULL || result->measurement_id == 0u || result->measurement_id < facts->measurement.measurement_id
        || config_version == 0u || config_version > UINT64_C(9007199254740991)
        || observed_ms > now.captured_uptime_ms || observed_ms < facts->measurement_observed_ms
        || result->elapsed_ms > 5000u || result->elapsed_ms > observed_ms || result->sample_count > 32u
        || result->status > WEIGHT_MEASUREMENT_INTERRUPTED) return 0u;
    success = (uint8_t)(result->status == WEIGHT_MEASUREMENT_STABLE_MEAN || result->status == WEIGHT_MEASUREMENT_TIMEOUT_MEDIAN);
    if (result->value_available != success) return 0u;
    if (success) {
        if (result->sample_count < 5u
            || (result->status == WEIGHT_MEASUREMENT_STABLE_MEAN && result->sample_span_grams > 100u)
            || (result->status == WEIGHT_MEASUREMENT_TIMEOUT_MEDIAN && result->elapsed_ms != 5000u)) return 0u;
    } else if (result->grams != 0 || result->sample_span_grams != 0u
        || (result->status == WEIGHT_MEASUREMENT_PENDING && result->elapsed_ms != 0u)) return 0u;
    if (result->measurement_id == facts->measurement.measurement_id) {
        if (config_version != facts->measurement_config_version) return 0u;
        if (facts->measurement.status != WEIGHT_MEASUREMENT_PENDING)
            return same_measurement(result, &facts->measurement); /* Do not refresh a terminal observation. */
    }
    facts->measurement = *result;
    facts->measurement_config_version = config_version;
    facts->measurement_observed_ms = observed_ms;
    return 1u;
}

static uint8_t publish_scale(McuDeviceFacts *facts, uint32_t sequence, uint64_t captured_ms,
    uint32_t calibration, uint8_t status, int32_t grams) {
    ActuatorSnapshot now = ActuatorRuntime_Snapshot();
    if (sequence == 0u || sequence <= facts->scale_attempt
        || captured_ms < facts->scale_captured_ms || captured_ms > now.captured_uptime_ms) return 0u;
    facts->scale_attempt = sequence;
    facts->scale_captured_ms = captured_ms;
    facts->scale_calibration = calibration;
    facts->scale_status = status;
    facts->scale_grams = status == ECOBIN_UART_SCALE_READ_STATUS_VALID ? grams : 0;
    return 1u;
}

uint8_t McuDeviceFacts_PublishScaleObservation(McuDeviceFacts *facts, const ScaleReaderObservation *observation) {
    uint8_t status;
    if (facts == NULL || observation == NULL || facts->port_no == 0u
        || observation->port_no != facts->port_no || observation->attempt_sequence == 0u
        || (observation->status != SCALE_READER_OK && observation->grams != 0)) return 0u;
    switch (observation->status) {
    case SCALE_READER_OK: status = ECOBIN_UART_SCALE_READ_STATUS_VALID; break;
    case SCALE_READER_TIMEOUT: status = ECOBIN_UART_SCALE_READ_STATUS_TIMEOUT; break;
    case SCALE_READER_CRC_ERROR: status = ECOBIN_UART_SCALE_READ_STATUS_CRC_ERROR; break;
    case SCALE_READER_RANGE_ERROR: status = ECOBIN_UART_SCALE_READ_STATUS_RANGE_ERROR; break;
    case SCALE_READER_PROTOCOL_ERROR: status = ECOBIN_UART_SCALE_READ_STATUS_PROTOCOL_ERROR; break;
    default: return 0u;
    }
    if (observation->attempt_sequence == facts->scale_attempt)
        return (uint8_t)(observation->captured_ms == facts->scale_captured_ms
            && observation->calibration_version == facts->scale_calibration
            && observation->grams == facts->scale_grams && status == facts->scale_status);
    return publish_scale(facts, observation->attempt_sequence, observation->captured_ms,
        observation->calibration_version, status, observation->grams);
}

uint8_t McuDeviceFacts_ObserveScale(McuDeviceFacts *facts, uint32_t attempt_sequence,
    uint64_t captured_ms, uint32_t calibration_version, const uint8_t *frame,
    size_t length, int32_t minimum_grams, int32_t maximum_grams) {
    int32_t grams = 0;
    uint8_t decoded = SCALE_READER_PROTOCOL_ERROR, status;
    if (minimum_grams > maximum_grams) return 0u;
    if (frame != NULL && length <= 255u)
        decoded = ScaleReader_Decode(frame, (uint8_t)length, minimum_grams, maximum_grams, &grams);
    switch (decoded) {
    case SCALE_READER_OK: status = ECOBIN_UART_SCALE_READ_STATUS_VALID; break;
    case SCALE_READER_CRC_ERROR: status = ECOBIN_UART_SCALE_READ_STATUS_CRC_ERROR; break;
    case SCALE_READER_RANGE_ERROR: status = ECOBIN_UART_SCALE_READ_STATUS_RANGE_ERROR; break;
    default: status = ECOBIN_UART_SCALE_READ_STATUS_PROTOCOL_ERROR; break;
    }
    return publish_scale(facts, attempt_sequence, captured_ms, calibration_version, status, grams);
}

uint8_t McuDeviceFacts_ScaleTimeout(McuDeviceFacts *facts, uint32_t attempt_sequence,
    uint64_t captured_ms, uint32_t calibration_version) {
    return publish_scale(facts, attempt_sequence, captured_ms, calibration_version,
        ECOBIN_UART_SCALE_READ_STATUS_TIMEOUT, 0);
}

uint8_t McuDeviceFacts_PublishSmoke(McuDeviceFacts *facts, uint8_t state, uint64_t observed_ms) {
    ActuatorSnapshot now;
    if (facts == NULL || state < ECOBIN_UART_SMOKE_OBSERVATION_STATE_NORMAL
        || state > ECOBIN_UART_SMOKE_OBSERVATION_STATE_UNAVAILABLE) return 0u;
    now = ActuatorRuntime_Snapshot();
    if (observed_ms > now.captured_uptime_ms || observed_ms < facts->smoke_observed_ms) return 0u;
    if (facts->smoke_state != 0u && observed_ms == facts->smoke_observed_ms)
        return (uint8_t)(state == facts->smoke_state);
    facts->smoke_state = state;
    facts->smoke_observed_ms = observed_ms;
    return 1u;
}

uint8_t McuDeviceFacts_PublishFullness(McuDeviceFacts *facts, uint8_t kind, uint8_t status,
    uint64_t captured_ms, uint8_t infrared_blocked, uint16_t distance_mm) {
    ActuatorSnapshot now;
    if (facts == NULL || kind < ECOBIN_UART_FULLNESS_OBSERVATION_KIND_ULTRASONIC
        || kind > ECOBIN_UART_FULLNESS_OBSERVATION_KIND_DIGITAL_INFRARED
        || status < ECOBIN_UART_FULLNESS_READ_STATUS_VALID || status > ECOBIN_UART_FULLNESS_READ_STATUS_UNAVAILABLE
        || infrared_blocked > 1u
        || ((status != ECOBIN_UART_FULLNESS_READ_STATUS_VALID
                || kind != ECOBIN_UART_FULLNESS_OBSERVATION_KIND_DIGITAL_INFRARED) && infrared_blocked != 0u)
        || ((status != ECOBIN_UART_FULLNESS_READ_STATUS_VALID
                || kind != ECOBIN_UART_FULLNESS_OBSERVATION_KIND_ULTRASONIC) && distance_mm != 0u)) return 0u;
    now = ActuatorRuntime_Snapshot();
    if (captured_ms > now.captured_uptime_ms || captured_ms < facts->fullness_captured_ms) return 0u;
    if (facts->fullness_status != 0u && captured_ms == facts->fullness_captured_ms)
        return (uint8_t)(kind == facts->fullness_kind && status == facts->fullness_status
            && infrared_blocked == facts->fullness_infrared_blocked && distance_mm == facts->fullness_distance_mm);
    facts->fullness_kind = kind;
    facts->fullness_status = status;
    facts->fullness_captured_ms = captured_ms;
    facts->fullness_infrared_blocked = infrared_blocked;
    facts->fullness_distance_mm = distance_mm;
    return 1u;
}

size_t McuDeviceFacts_Capture(const McuDeviceFacts *facts, const McuWorkState *work,
    const uint8_t *request, size_t length, uint8_t *output, size_t capacity) {
    ActuatorSnapshot actuator;
    uint8_t status, retained = 0u;
    static const uint8_t measurement_states[] = {
        ECOBIN_UART_MEASUREMENT_OBSERVATION_STATE_RUNNING,
        ECOBIN_UART_MEASUREMENT_OBSERVATION_STATE_STABLE_MEAN,
        ECOBIN_UART_MEASUREMENT_OBSERVATION_STATE_TIMEOUT_MEDIAN,
        ECOBIN_UART_MEASUREMENT_OBSERVATION_STATE_UNAVAILABLE,
        ECOBIN_UART_MEASUREMENT_OBSERVATION_STATE_CONFIG_ERROR,
        ECOBIN_UART_MEASUREMENT_OBSERVATION_STATE_BUFFER_FULL,
        ECOBIN_UART_MEASUREMENT_OBSERVATION_STATE_INTERRUPTED
    };
    if (facts == NULL || work == NULL || request == NULL || output == NULL
        || length != ECOBIN_UART_QUERY_DEVICE_FACTS_PAYLOAD_MAX_LENGTH
        || capacity < ECOBIN_UART_DEVICE_FACTS_REPLY_PAYLOAD_MAX_LENGTH
        || ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_QUERY_DEVICE_FACTS,
            request, (uint16_t)length) != 0) return 0u;
    memset(output, 0, ECOBIN_UART_DEVICE_FACTS_REPLY_PAYLOAD_MAX_LENGTH);
    memcpy(output, request, length);
    U64(CURRENT_MCU_BOOT_ID, work->result.boot_id);
    status = ecobin_uart_read_u64_be(request + ECOBIN_UART_QUERY_DEVICE_FACTS_TARGET_MCU_BOOT_ID_OFFSET) != work->result.boot_id
        ? ECOBIN_UART_DEVICE_FACTS_STATUS_BOOT_MISMATCH
        : request[ECOBIN_UART_QUERY_DEVICE_FACTS_PORT_NO_OFFSET] != facts->port_no
        ? ECOBIN_UART_DEVICE_FACTS_STATUS_PORT_UNSUPPORTED : ECOBIN_UART_DEVICE_FACTS_STATUS_AVAILABLE;
    U8(STATUS, status);
    if (status == ECOBIN_UART_DEVICE_FACTS_STATUS_AVAILABLE) {
        actuator = ActuatorRuntime_Snapshot();
        U64(CAPTURED_UPTIME_MS, actuator.captured_uptime_ms);
        U64(CONTROL_UPTIME_MS, actuator.control_uptime_ms);
        U8(LAST_DELIVERY_DOOR_COMMAND, !actuator.door.target_valid ? ECOBIN_UART_DELIVERY_DOOR_COMMAND_NONE
            : actuator.door.target == MCU_DIRECTION_OPEN ? ECOBIN_UART_DELIVERY_DOOR_COMMAND_OPEN : ECOBIN_UART_DELIVERY_DOOR_COMMAND_CLOSE);
        U8(DOOR_ACTION_ACTIVE, actuator.door.action_active);
        U8(PB6_OUTPUT, actuator.door.pb6); U8(PB7_OUTPUT, actuator.door.pb7);
        U8(PB5_ACTIVE, actuator.pinch_input_active); U8(PINCH_PAUSED, actuator.door.pinch_paused);
        U8(CLEAN_LOCK_POWERED, actuator.lock_powered); U8(UPDATE_LATCHED, actuator.update_latched);
        U64(APPLIED_CONFIG_VERSION, facts->config_version);
        BYTES(APPLIED_CONTENT_SHA256, facts->content_sha256, 32u);
        BYTES(APPLIED_MCU_PAYLOAD_SHA256, facts->mcu_sha256, 32u);
        U8(CONFIG_STAGING, facts->config_staging);
        U8(SCALE_READ_STATUS, facts->scale_status); U32(SCALE_ATTEMPT_SEQUENCE, facts->scale_attempt);
        U64(SCALE_CAPTURED_UPTIME_MS, facts->scale_captured_ms); U32(SCALE_WEIGHT_GRAMS, facts->scale_grams);
        U32(SCALE_CALIBRATION_VERSION, facts->scale_calibration);
        if (facts->measurement.measurement_id != 0u) {
            if (facts->measurement.status >= sizeof(measurement_states) || facts->measurement.elapsed_ms > 5000u) return 0u;
            U32(MEASUREMENT_SEQUENCE, facts->measurement.measurement_id);
            U8(MEASUREMENT_STATE, measurement_states[facts->measurement.status]);
            U64(MEASUREMENT_CONFIG_VERSION, facts->measurement_config_version);
            U64(MEASUREMENT_OBSERVED_UPTIME_MS, facts->measurement_observed_ms);
            U16(MEASUREMENT_ELAPSED_MS, facts->measurement.elapsed_ms);
            U8(MEASUREMENT_SAMPLE_COUNT, facts->measurement.sample_count);
            U32(MEASUREMENT_WEIGHT_GRAMS, facts->measurement.grams);
            U32(MEASUREMENT_SPAN_GRAMS, facts->measurement.sample_span_grams);
        }
        U8(SMOKE_OBSERVATION_STATE, facts->smoke_state);
        U64(SMOKE_OBSERVED_UPTIME_MS, facts->smoke_observed_ms);
        U8(FULLNESS_OBSERVATION_KIND, facts->fullness_kind);
        U8(FULLNESS_READ_STATUS, facts->fullness_status);
        U64(FULLNESS_CAPTURED_UPTIME_MS, facts->fullness_captured_ms);
        U8(FULLNESS_INFRARED_BLOCKED, facts->fullness_infrared_blocked);
        U16(FULLNESS_DISTANCE_MM, facts->fullness_distance_mm);
        switch (work->status) {
        case 0: break;
        case ECOBIN_UART_WORK_QUERY_STATUS_RUNNING: retained = ECOBIN_UART_RETAINED_WORK_STATE_RUNNING; break;
        case ECOBIN_UART_WORK_QUERY_STATUS_RESULT_HELD: retained = ECOBIN_UART_RETAINED_WORK_STATE_RESULT_HELD; break;
        case ECOBIN_UART_WORK_QUERY_STATUS_RESULT_RELEASED: retained = ECOBIN_UART_RETAINED_WORK_STATE_RESULT_RELEASED; break;
        default: return 0u;
        }
        U8(RETAINED_WORK_STATE, retained);
        if (retained != 0u) {
            BYTES(RETAINED_WORK_UID, work->identity + WORK_OFFSET(WORK_UID), 16u);
            U8(RETAINED_WORK_TYPE, work->identity[WORK_OFFSET(WORK_TYPE)]);
            U8(RETAINED_PORT_NO, work->identity[WORK_OFFSET(PORT_NO)]);
            U8(RETAINED_WORK_PHASE, work->phase);
            BYTES(RETAINED_ORIGIN_COMMAND_SEQUENCE, work->identity + WORK_OFFSET(COMMAND_SEQUENCE), 4u);
            if (retained != ECOBIN_UART_RETAINED_WORK_STATE_RUNNING)
                U32(RETAINED_RESULT_SEQUENCE, work->result.highest_sequence);
        }
    }
    return ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_DEVICE_FACTS_REPLY, output,
        ECOBIN_UART_DEVICE_FACTS_REPLY_PAYLOAD_MAX_LENGTH) == 0 ? ECOBIN_UART_DEVICE_FACTS_REPLY_PAYLOAD_MAX_LENGTH : 0u;
}
