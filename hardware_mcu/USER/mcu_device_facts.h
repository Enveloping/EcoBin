#ifndef ECOBIN_MCU_DEVICE_FACTS_H
#define ECOBIN_MCU_DEVICE_FACTS_H
#include "mcu_work_state.h"
#include "weight_measurement.h"
#include "scale_reader.h"

/* Candidate foreground-only publication cache. Init on actual MCU boot before
 * binding, not on Pi reconnect. One port's controller; not a multi-port atomic snapshot.
 * Producers and Capture share one foreground owner. Never modify this in ISR.
 * Sensor ingress must first establish request ownership and complete-frame
 * capture time; this module cannot disambiguate late unnumbered Modbus frames.
 */
typedef struct {
    uint64_t config_version;
    uint8_t content_sha256[32], mcu_sha256[32];
    uint8_t config_staging, port_no;
    uint32_t scale_attempt;
    uint64_t scale_captured_ms;
    int32_t scale_grams;
    uint32_t scale_calibration;
    uint8_t scale_status;
    WeightMeasurementResult measurement;
    uint64_t measurement_config_version, measurement_observed_ms;
    uint64_t smoke_observed_ms, fullness_captured_ms;
    uint16_t fullness_distance_mm;
    uint8_t smoke_state, fullness_kind, fullness_status, fullness_infrared_blocked;
} McuDeviceFacts;

void McuDeviceFacts_Init(McuDeviceFacts *facts, uint8_t port_no);
/* Called by the future configuration owner ONLY AFTER real apply completes.
 * Records facts, does not apply or validate the configuration's full body.
 * Same version with different digests and version rollback are rejected.
 */
uint8_t McuDeviceFacts_PublishConfiguration(McuDeviceFacts *facts, uint64_t version,
    const uint8_t *content_sha256, const uint8_t *mcu_sha256, uint8_t staging);
/* Owner publishes actual measurement-core observations; no Poll/measurement
 * is triggered by Capture. Latest record is historical, NOT current health.
 * Same measurement sequence cannot change config or overwrite a terminal result.
 * observed_ms is when the owner observed the core result, NOT a new raw sample.
 */
uint8_t McuDeviceFacts_PublishMeasurement(McuDeviceFacts *facts, const WeightMeasurementResult *result,
    uint64_t config_version, uint64_t observed_ms);
/* Publish a completed, locally owned RS485 attempt. Validates the real Modbus
 * bytes; errors replace current read status and do NOT reuse an earlier weight.
 * attempt_sequence is monotonic/no-wrap within the boot; time is complete-frame
 * capture, never delayed main-loop processing time. No serial I/O here.
 */
uint8_t McuDeviceFacts_ObserveScale(McuDeviceFacts *facts, uint32_t attempt_sequence,
    uint64_t captured_ms, uint32_t calibration_version, const uint8_t *frame,
    size_t length, int32_t minimum_grams, int32_t maximum_grams);
uint8_t McuDeviceFacts_ScaleTimeout(McuDeviceFacts *facts, uint32_t attempt_sequence,
    uint64_t captured_ms, uint32_t calibration_version);
/* Transfer the owned reader's already decoded observation for this port.
 * Exact re-publication is idempotent without refreshing capture time; stale,
 * foreign-port or conflicting observations are rejected. No sensor I/O. */
uint8_t McuDeviceFacts_PublishScaleObservation(McuDeviceFacts *facts, const ScaleReaderObservation *observation);
/* Foreground producers publish their actual completed observations, not polling
 * time. Smoke uses the existing debounced monitor state (NORMAL/ALARM/UNAVAILABLE),
 * mapped explicitly to the native enum; these codes are NOT old CC codes.
 * Fullness preserves sensor kind and raw distance / digital blocked level.
 * No echo / read failure is UNAVAILABLE, never a fabricated CLEAR or distance.
 * Same-time identical publication is idempotent; conflicting or older evidence
 * is rejected. Call only after real acquisition; neither API reads hardware.
 */
uint8_t McuDeviceFacts_PublishSmoke(McuDeviceFacts *facts, uint8_t state, uint64_t observed_ms);
uint8_t McuDeviceFacts_PublishFullness(McuDeviceFacts *facts, uint8_t kind, uint8_t status,
    uint64_t captured_ms, uint8_t infrared_blocked, uint16_t distance_mm);
/* Requires initialized ActuatorRuntime. Output is an external non-overlapping
 * complete DEVICE_FACTS_REPLY payload. The only IRQ-masked part is the bounded
 * actuator/time copy; encoding/validation run after restoring interrupts.
 * No GPIO write, fresh measurement, result ACK, work release or admission.
 */
size_t McuDeviceFacts_Capture(const McuDeviceFacts *facts, const McuWorkState *work,
    const uint8_t *request, size_t length, uint8_t *output, size_t capacity);
#endif
