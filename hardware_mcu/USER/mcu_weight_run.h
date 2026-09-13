#ifndef ECOBIN_MCU_WEIGHT_RUN_H
#define ECOBIN_MCU_WEIGHT_RUN_H
#include "mcu_config_collection.h"
#include "scale_reader.h"

/* Candidate single-foreground measurement owner, no serial/GPIO/Flash I/O.
 * Init ONLY on MCU boot, not Pi reconnect/phase change; counters never wrap.
 * Begin expects the typed projection of a completely verified configuration,
 * e.g. McuConfiguration_ReadWeightPolicy, NOT direct HMI/cloud input. The owner
 * must still check staging, whole-device apply, accepted work and port routing.
 * Boundary checks here do not replace the authoritative full config validator.
 * A run copies that policy, including its original config/calibration versions.
 * This is a configuration CONSUMER, not proof of whole-device APPLIED.
 * The business owner supplies the accepted phase's local measurement sequence.
 * Local attempt numbers are NOT echoed by the scale. Start/FinishOwnedAttempt
 * require the ingress owner to establish exclusive request/response ownership.
 * In particular, after timeout, merely flushing UART or assigning another local
 * number cannot prove that a late unnumbered response belongs to a new request.
 * Do not connect a generic RX callback by relabelling it with the current number.
 */
typedef struct {
    McuConfigWeightPolicy policy;
    WeightMeasurement measurement;
    ScaleReaderObservation observation;
    uint64_t started_ms, last_now_ms, attempt_started_ms;
    uint32_t attempt_sequence;
    uint8_t present, in_flight, has_started_attempt, retired;
} McuWeightRun;

void McuWeightRun_Init(McuWeightRun *run);
uint8_t McuWeightRun_Begin(McuWeightRun *run, const McuConfigWeightPolicy *policy,
    uint32_t measurement_sequence, uint64_t now_ms);
/* Reserve a single actual request start. 0 means not due/not allowed. No TX or
 * permission grant here. Caller must satisfy ingress ownership before calling.
 */
uint32_t McuWeightRun_StartOwnedAttempt(McuWeightRun *run, uint64_t now_ms);
/* Complete-frame capture time, not foreground processing time. Drain all owned
 * captures in arrival order before Poll. 1 means this attempt was consumed,
 * including invalid sensor data; it does not mean a weight was obtained.
 */
uint8_t McuWeightRun_FinishOwnedAttempt(McuWeightRun *run,
    uint32_t measurement_sequence, uint32_t attempt_sequence,
    uint64_t captured_ms, uint64_t now_ms, const uint8_t *frame, size_t length);
/* Closes locally expired attempts and freezes the phase at its hard deadline.
 * This does NOT establish that the physical serial channel is ready for reuse.
 * Use actual monotonic 64-bit uptime. Drain captured responses BEFORE calling.
 */
uint8_t McuWeightRun_Poll(McuWeightRun *run, uint64_t now_ms);
/* End this exact pending acquisition at current foreground time. Preserves a
 * result already terminal/due; never backdates or extends the original window.
 * Closes the local in-flight attempt, not the physical RS485 ambiguity. Caller
 * must still retain/retire the terminal data; no new sample or request is made. */
uint8_t McuWeightRun_Interrupt(McuWeightRun *run, uint32_t measurement_sequence, uint64_t now_ms);
/* Read-only copy of the current result AND the original immutable policy.
 * Pending is not a result; available=0 is not a measured zero. Outputs must be
 * external/non-overlapping. A retired record remains readable until next Begin.
 */
uint8_t McuWeightRun_Copy(const McuWeightRun *run, WeightMeasurementResult *result,
    McuConfigWeightPolicy *policy);
/* Latest actual completed read, independent of the historical measurement.
 * No read is started and no timestamp refreshed; returns 0 without modifying
 * output until an owned attempt completes or actually times out. A timeout
 * carries its original response deadline, not delayed foreground time. A
 * request cancelled by phase completion/interruption does not imply timeout.
 * Retire/Begin preserve the observation and its original calibration/port. */
uint8_t McuWeightRun_CopyObservation(const McuWeightRun *run, ScaleReaderObservation *output);
/* Local working-buffer retirement ONLY after the business owner has retained
 * the exact terminal record (e.g. process slot Freeze succeeded). Not a wire
 * RESULT_SAVED, business release, or proof of Pi persistence. Pending cannot
 * retire. Counters and last request start survive this operation.
 */
uint8_t McuWeightRun_Retire(McuWeightRun *run, uint32_t measurement_sequence);
#endif
