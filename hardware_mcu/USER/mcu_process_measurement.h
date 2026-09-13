#ifndef ECOBIN_MCU_PROCESS_MEASUREMENT_H
#define ECOBIN_MCU_PROCESS_MEASUREMENT_H
#include "mcu_result_builder.h"
#include "mcu_fullness_run.h"

typedef struct {
    uint64_t config_version, observed_uptime_ms;
    /* Delivery round or clean finish-action sequence; zero for pre-unlock.
     * These process fields remain u16 on the wire: reject, never truncate.
     */
    uint32_t step_sequence;
} McuProcessMeasurementMeta;

/* Pure foreground encoder for four delivery/clean measurement events. Derives
 * work/command/port identity from the retained accepted work, checks exact boot
 * and measuring phase, uses the SAME McuResultMeasurement as final assembly.
 * Owner supplies actual frozen config/time/measurement identity. Encode once
 * before advancing phase; retain the original bytes for delivery/recovery.
 * Caller-owned non-overlapping scratch; returns payload length or zero. Failure
 * may alter scratch but never work/measurement/meta. No UID allocation, event
 * retention, ACK, send, measurement, GPIO, work release or authorization here.
 * FULLNESS uses the work-event extension below. BASELINE has a separate
 * command-scoped encoder because it is not a delivery/clean work state.
 */
size_t McuProcessMeasurement_BuildWorkEvent(const McuWorkState *work,
    const McuResultMeasurement *measurement, const McuProcessMeasurementMeta *meta,
    uint8_t message_type, uint8_t *scratch, size_t capacity);
/* Extended terminal post-close/clean-final record, from the still-held actual
 * group owner and its frozen policy (not caller-supplied replacement settings).
 * The original weight timestamp remains unchanged; sensor times are separate.
 * Base encoder explicitly emits NOT_SAMPLED when there was no sensor group.
 * Neither encoder samples/retires, allocates events or implies Pi custody. */
size_t McuProcessMeasurement_BuildWorkEventWithFullness(const McuWorkState *work,
    const McuResultMeasurement *measurement, const McuProcessMeasurementMeta *meta,
    const McuFullnessRun *fullness, uint8_t message_type, uint8_t *scratch, size_t capacity);
/* Pure encoder for one accepted MEASURE_BASELINE scope. The scope is the
 * QUERY_PROCESS_EVENT payload without queryId and remains owned by the caller.
 * measurementUid in that scope identifies the baseline task; the result's uid
 * identifies the actual boot-local weight acquisition. No work/result state,
 * GPIO, ACK, retirement or recovery semantics are created here. */
size_t McuProcessMeasurement_BuildBaselineEvent(const uint8_t *scope, size_t scope_length,
    const McuResultMeasurement *measurement, const McuProcessMeasurementMeta *meta,
    uint8_t *scratch, size_t capacity);
#endif
