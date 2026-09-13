#include "mcu_result_builder.h"
#include <string.h>

#define OFFSET(field) ECOBIN_UART_WORK_RESULT_##field##_OFFSET
#define ID_OFFSET(field) (ECOBIN_UART_QUERY_WORK_##field##_OFFSET - ECOBIN_UART_QUERY_WORK_MCU_COMMAND_UID_OFFSET)
#define MEAS_OFFSET(field) (OFFSET(INITIAL_##field) - OFFSET(INITIAL_KIND))

uint8_t McuResultMeasurement_FromAvailable(McuResultMeasurement *output,
    const WeightMeasurementResult *result, const uint8_t *measurement_uid,
    uint64_t boot_id, uint32_t event_sequence, uint32_t calibration_version) {
    uint8_t stable;
    if (output == NULL || result == NULL || measurement_uid == NULL
        || result->measurement_id == 0u || boot_id == 0u || boot_id > UINT64_C(9007199254740991)
        || event_sequence == 0u || ecobin_uart_bytes_zero(measurement_uid, 16u)
        || result->value_available != 1u || result->sample_count < 5u || result->sample_count > 32u
        || result->elapsed_ms > 5000u) return 0u;
    stable = result->status == WEIGHT_MEASUREMENT_STABLE_MEAN;
    if (stable ? result->sample_span_grams > 100u
        : (result->status != WEIGHT_MEASUREMENT_TIMEOUT_MEDIAN || result->elapsed_ms != 5000u)) return 0u;
    memset(output, 0, sizeof(*output));
    output->kind = stable ? ECOBIN_UART_RESULT_MEASUREMENT_KIND_STABLE_MEAN : ECOBIN_UART_RESULT_MEASUREMENT_KIND_TIMEOUT_MEDIAN;
    memcpy(output->uid, measurement_uid, sizeof(output->uid));
    output->source_boot_id = boot_id;
    output->event_sequence = event_sequence;
    output->grams = result->grams;
    output->elapsed_ms = (uint16_t)result->elapsed_ms;
    output->sample_count = result->sample_count;
    output->span_grams = result->sample_span_grams;
    output->calibration_version = calibration_version;
    return 1u;
}

static void encode_measurement(uint8_t *output, const McuResultMeasurement *measurement) {
    output[MEAS_OFFSET(KIND)] = measurement->kind;
    memcpy(output + MEAS_OFFSET(MEASUREMENT_UID), measurement->uid, sizeof(measurement->uid));
    ecobin_uart_write_u64_be(output + MEAS_OFFSET(SOURCE_MCU_BOOT_ID), measurement->source_boot_id);
    ecobin_uart_write_u32_be(output + MEAS_OFFSET(MCU_EVENT_SEQUENCE), measurement->event_sequence);
    ecobin_uart_write_u32_be(output + MEAS_OFFSET(WEIGHT_GRAMS), (uint32_t)measurement->grams);
    ecobin_uart_write_u16_be(output + MEAS_OFFSET(ELAPSED_MS), measurement->elapsed_ms);
    output[MEAS_OFFSET(SAMPLE_COUNT)] = measurement->sample_count;
    ecobin_uart_write_u32_be(output + MEAS_OFFSET(SPAN_GRAMS), measurement->span_grams);
    ecobin_uart_write_u32_be(output + MEAS_OFFSET(CALIBRATION_VERSION), measurement->calibration_version);
    ecobin_uart_write_u16_be(output + MEAS_OFFSET(FAULT_CODE), measurement->fault_code);
}

uint8_t McuResultBuilder_Complete(McuWorkState *state, const McuResultSummary *summary,
    const McuResultMeasurement *initial, const McuResultMeasurement *final,
    uint8_t *scratch, size_t capacity) {
    uint32_t sequence;
    if (state == NULL || summary == NULL || initial == NULL || final == NULL || scratch == NULL
        || capacity < ECOBIN_UART_WORK_RESULT_PAYLOAD_MAX_LENGTH) return 0u;
    sequence = state->result.highest_sequence;
    if (state->status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING) {
        if (sequence == UINT32_MAX) return 0u;
        sequence++;
    } else if (state->status != ECOBIN_UART_WORK_QUERY_STATUS_RESULT_HELD) return 0u;
    memset(scratch, 0, ECOBIN_UART_WORK_RESULT_PAYLOAD_MAX_LENGTH);
    ecobin_uart_write_u64_be(scratch + OFFSET(MCU_BOOT_ID), state->result.boot_id);
    ecobin_uart_write_u32_be(scratch + OFFSET(RESULT_SEQUENCE), sequence);
    memcpy(scratch + OFFSET(WORK_UID), state->identity + ID_OFFSET(WORK_UID), 16u);
    scratch[OFFSET(WORK_TYPE)] = state->identity[ID_OFFSET(WORK_TYPE)];
    scratch[OFFSET(PORT_NO)] = state->identity[ID_OFFSET(PORT_NO)];
    ecobin_uart_write_u64_be(scratch + OFFSET(CONFIG_VERSION), summary->config_version);
    memcpy(scratch + OFFSET(ORIGIN_COMMAND_UID), state->identity + ID_OFFSET(MCU_COMMAND_UID), 16u);
    memcpy(scratch + OFFSET(ORIGIN_COMMAND_SEQUENCE), state->identity + ID_OFFSET(COMMAND_SEQUENCE), 4u);
    ecobin_uart_write_u64_be(scratch + OFFSET(COMPLETED_UPTIME_MS), summary->completed_uptime_ms);
    ecobin_uart_write_u16_be(scratch + OFFSET(DELIVERY_ROUND_COUNT), summary->delivery_round_count);
    ecobin_uart_write_u32_be(scratch + OFFSET(CLEAN_ACTION_SEQUENCE), summary->clean_action_sequence);
    scratch[OFFSET(FINISH_REASON)] = summary->finish_reason;
    scratch[OFFSET(PHYSICAL_CLOSE_CONFIRMED)] = summary->physical_close_confirmed;
    scratch[OFFSET(NEGATIVE_WEIGHT_ANOMALY)] = summary->negative_weight_anomaly;
    encode_measurement(scratch + OFFSET(INITIAL_KIND), initial);
    encode_measurement(scratch + OFFSET(FINAL_KIND), final);
    ecobin_uart_compute_result_digest(scratch, scratch + OFFSET(RESULT_DIGEST_SHA256));
    return McuWorkState_Complete(state, scratch, ECOBIN_UART_WORK_RESULT_PAYLOAD_MAX_LENGTH);
}
