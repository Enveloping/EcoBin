#ifndef ECOBIN_WEIGHT_MEASUREMENT_H
#define ECOBIN_WEIGHT_MEASUREMENT_H
#include <stdint.h>

/* Internal measurement interface, NOT UART enum values or wire payload. */
#define WEIGHT_MEASUREMENT_CAPACITY 32U
#define WEIGHT_MEASUREMENT_PENDING 0U
#define WEIGHT_MEASUREMENT_STABLE_MEAN 1U
#define WEIGHT_MEASUREMENT_TIMEOUT_MEDIAN 2U
#define WEIGHT_MEASUREMENT_UNAVAILABLE 3U
#define WEIGHT_MEASUREMENT_CONFIG_ERROR 4U
#define WEIGHT_MEASUREMENT_BUFFER_FULL 5U
#define WEIGHT_MEASUREMENT_INTERRUPTED 6U

typedef struct {
    uint32_t timeout_ms;
    uint32_t stable_window_ms;
    uint32_t maximum_age_ms;
    uint32_t maximum_span_grams;
    uint8_t stable_samples;
    uint8_t minimum_median_samples;
    int32_t minimum_grams;
    int32_t maximum_grams;
} WeightMeasurementConfig;
typedef struct {
    uint32_t measurement_id;
    uint32_t elapsed_ms;
    uint32_t sample_span_grams;
    int32_t grams;
    uint8_t status;
    uint8_t value_available;
    uint8_t sample_count;
} WeightMeasurementResult;
typedef struct {
    WeightMeasurementConfig config;
    WeightMeasurementResult result;
    uint32_t started_ms;
    uint32_t last_sample_id;
    uint32_t received_ms[WEIGHT_MEASUREMENT_CAPACITY];
    int32_t samples[WEIGHT_MEASUREMENT_CAPACITY];
    uint8_t count;
} WeightMeasurement;

/* Side-effect-free preflight for owners that must retain an earlier result. */
uint8_t WeightMeasurement_ConfigValid(const WeightMeasurementConfig *config);
/* Begin explicitly starts a new phase. The work owner must retain prior results
 * until saved; Observe/Poll themselves never overwrite a frozen result.
 * Parameters will come from the native applied configuration, not HMI inputs.
 * after_sample_id is the reader's latest sequence when this phase begins. */
uint8_t WeightMeasurement_Begin(WeightMeasurement *measurement,
    const WeightMeasurementConfig *config, uint32_t measurement_id,
    uint32_t now, uint32_t after_sample_id);
/* Observe/Poll/Interrupt require an object initialized by Begin, not a raw zero
 * struct or caller-mutated configuration. Drain validated responses in arrival
 * order BEFORE Poll, including responses
 * fully captured within the deadline but processed later. received_ms is the
 * actual complete-frame capture time, not the main-loop processing time.
 * Responses captured after the deadline are rejected. Freshness is evaluated
 * at min(now, deadline); processing delay never extends the measurement window.
 * Timestamps and sequence originate locally; never use remote/untrusted time. */
uint8_t WeightMeasurement_Observe(WeightMeasurement *measurement,
    uint32_t measurement_id, uint32_t sample_id, uint32_t received_ms,
    int32_t grams, uint32_t now);
WeightMeasurementResult WeightMeasurement_Poll(WeightMeasurement *measurement,
    uint32_t now);
/* Foreground interruption at the actual observation time, not a backdated ISR
 * timestamp. Drain owned captures before this call. An already terminal result
 * (including a due timeout) is immutable; pending samples remain diagnostic only. */
WeightMeasurementResult WeightMeasurement_Interrupt(WeightMeasurement *measurement,
    uint32_t now);
#endif
