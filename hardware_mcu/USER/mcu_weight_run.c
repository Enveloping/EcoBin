#include "mcu_weight_run.h"
#include "scale_reader.h"
#include <string.h>

typedef char weight_run_ram_budget[(sizeof(McuWeightRun) <= 512u) ? 1 : -1];

static uint32_t elapsed(const McuWeightRun *run, uint64_t now) {
    uint64_t delta = now - run->started_ms;
    return delta > run->policy.measurement.timeout_ms ? run->policy.measurement.timeout_ms : (uint32_t)delta;
}

static void observe(McuWeightRun *run, uint64_t captured, uint8_t status, int32_t grams) {
    run->observation.captured_ms = captured;
    run->observation.attempt_sequence = run->attempt_sequence;
    run->observation.calibration_version = run->policy.calibration_version;
    run->observation.port_no = run->policy.port_no;
    run->observation.status = status;
    run->observation.grams = status == SCALE_READER_OK ? grams : 0;
}

void McuWeightRun_Init(McuWeightRun *run) {
    memset(run, 0, sizeof(*run));
}

uint8_t McuWeightRun_Begin(McuWeightRun *run, const McuConfigWeightPolicy *policy,
    uint32_t sequence, uint64_t now) {
    if (run == NULL || policy == NULL || (run->present && !run->retired)
        || sequence == 0u || sequence <= run->measurement.result.measurement_id
        || now < run->last_now_ms || policy->enabled != 1u || policy->port_no == 0u || policy->port_no > 6u
        || policy->config_version == 0u || policy->config_version > UINT64_C(9007199254740991)
        || policy->poll_interval_ms == 0u || policy->poll_interval_ms > policy->measurement.timeout_ms
        || policy->response_timeout_ms == 0u || policy->response_timeout_ms > policy->poll_interval_ms) return 0u;
    if (!WeightMeasurement_ConfigValid(&policy->measurement)) return 0u;
    WeightMeasurement_Begin(&run->measurement, &policy->measurement, sequence, 0u, run->attempt_sequence);
    run->policy = *policy;
    run->started_ms = run->last_now_ms = now;
    run->present = 1u;
    run->retired = 0u;
    return 1u;
}

uint32_t McuWeightRun_StartOwnedAttempt(McuWeightRun *run, uint64_t now) {
    if (run == NULL || !run->present || run->retired || now < run->last_now_ms || run->in_flight
        || run->measurement.result.status != WEIGHT_MEASUREMENT_PENDING
        || now - run->started_ms >= run->policy.measurement.timeout_ms
        || (run->has_started_attempt && now - run->attempt_started_ms < run->policy.poll_interval_ms)
        || run->attempt_sequence == UINT32_MAX) return 0u;
    run->last_now_ms = run->attempt_started_ms = now;
    run->in_flight = 1u;
    run->has_started_attempt = 1u;
    return ++run->attempt_sequence;
}

uint8_t McuWeightRun_FinishOwnedAttempt(McuWeightRun *run, uint32_t sequence, uint32_t attempt,
    uint64_t captured, uint64_t now, const uint8_t *frame, size_t length) {
    int32_t grams = 0;
    uint8_t decoded;
    if (run == NULL || !run->present || !run->in_flight || now < run->last_now_ms
        || sequence != run->measurement.result.measurement_id || attempt != run->attempt_sequence
        || captured < run->attempt_started_ms || captured > now
        || captured - run->attempt_started_ms > run->policy.response_timeout_ms) return 0u;
    decoded = length <= 255u ? ScaleReader_Decode(frame, (uint8_t)length,
        run->policy.measurement.minimum_grams, run->policy.measurement.maximum_grams, &grams)
        : SCALE_READER_PROTOCOL_ERROR;
    run->in_flight = 0u;
    run->last_now_ms = now;
    observe(run, captured, decoded, grams);
    if (decoded == SCALE_READER_OK && captured - run->started_ms <= run->policy.measurement.timeout_ms)
        WeightMeasurement_Observe(&run->measurement, sequence, attempt,
            (uint32_t)(captured - run->started_ms), grams, elapsed(run, now));
    return 1u;
}

uint8_t McuWeightRun_Poll(McuWeightRun *run, uint64_t now) {
    if (run == NULL || !run->present || run->retired || now < run->last_now_ms) return 0u;
    run->last_now_ms = now;
    if (run->in_flight && now - run->attempt_started_ms >= run->policy.response_timeout_ms) {
        /* A phase ending before the response deadline cancels its request;
         * it is not evidence of a sensor timeout. Late polling cannot extend it. */
        if (run->attempt_started_ms - run->started_ms + run->policy.response_timeout_ms
            <= run->policy.measurement.timeout_ms)
            observe(run, run->attempt_started_ms + run->policy.response_timeout_ms, SCALE_READER_TIMEOUT, 0);
        run->in_flight = 0u;
    }
    WeightMeasurement_Poll(&run->measurement, elapsed(run, now));
    if (run->measurement.result.status != WEIGHT_MEASUREMENT_PENDING) run->in_flight = 0u;
    return 1u;
}

uint8_t McuWeightRun_Copy(const McuWeightRun *run, WeightMeasurementResult *result, McuConfigWeightPolicy *policy) {
    if (run == NULL || !run->present || result == NULL || policy == NULL) return 0u;
    *result = run->measurement.result;
    *policy = run->policy;
    return 1u;
}

uint8_t McuWeightRun_CopyObservation(const McuWeightRun *run, ScaleReaderObservation *output) {
    if (run == NULL || output == NULL || run->observation.attempt_sequence == 0u) return 0u;
    *output = run->observation;
    return 1u;
}

uint8_t McuWeightRun_Interrupt(McuWeightRun *run, uint32_t sequence, uint64_t now) {
    if (run == NULL || !run->present || run->retired || sequence != run->measurement.result.measurement_id
        || now < run->last_now_ms) return 0u;
    WeightMeasurement_Interrupt(&run->measurement, elapsed(run, now));
    run->last_now_ms = now;
    run->in_flight = 0u;
    return 1u;
}

uint8_t McuWeightRun_Retire(McuWeightRun *run, uint32_t sequence) {
    if (run == NULL || !run->present || sequence != run->measurement.result.measurement_id
        || run->measurement.result.status == WEIGHT_MEASUREMENT_PENDING) return 0u;
    run->retired = 1u;
    return 1u;
}
