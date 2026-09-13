#ifndef ECOBIN_MCU_RESULT_BUILDER_H
#define ECOBIN_MCU_RESULT_BUILDER_H
#include "mcu_work_state.h"
#include "weight_measurement.h"

/* Foreground-owned explicit facts, NOT a classifier or an authorization API.
 * UUID/event identity and timestamps come from the business/measurement owner.
 * A missing record is zero initialized with NOT_TAKEN or MCU_RESET_LOST kind;
 * it is not a measured zero. Failures must carry actual explicit fault facts.
 */
typedef struct {
    uint8_t kind, uid[16];
    uint64_t source_boot_id;
    uint32_t event_sequence;
    int32_t grams;
    uint16_t elapsed_ms;
    uint8_t sample_count;
    uint32_t span_grams, calibration_version;
    uint16_t fault_code;
} McuResultMeasurement;

typedef struct {
    uint64_t config_version, completed_uptime_ms;
    uint16_t delivery_round_count;
    uint32_t clean_action_sequence;
    uint8_t finish_reason, physical_close_confirmed, negative_weight_anomaly;
} McuResultSummary;

/* Adapts ONLY real available terminal core results. Does not turn pending,
 * timeout without readings, sensor failure or reset loss into a valid value.
 * Event sequence is separately supplied; the local measurement counter is
 * not silently reused as a UART event sequence. Output unchanged on failure.
 */
uint8_t McuResultMeasurement_FromAvailable(McuResultMeasurement *output,
    const WeightMeasurementResult *result, const uint8_t *measurement_uid,
    uint64_t boot_id, uint32_t event_sequence, uint32_t calibration_version);

/* Identity comes from the retained accepted original work, never new caller
 * values. Allocate the next RAM result sequence without wrap. Repeating an
 * already-held result succeeds only for identical assembled bytes. No Flash,
 * GPIO, serial, work admission, financial classification or business release.
 * scratch is caller-owned, external/non-overlapping, capacity >= 199; failure
 * may change scratch but never the retained work/result. Publish via CopyHeld.
 */
uint8_t McuResultBuilder_Complete(McuWorkState *state, const McuResultSummary *summary,
    const McuResultMeasurement *initial, const McuResultMeasurement *final,
    uint8_t *scratch, size_t capacity);
#endif
