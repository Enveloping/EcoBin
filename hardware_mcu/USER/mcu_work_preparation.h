#ifndef ECOBIN_MCU_WORK_PREPARATION_H
#define ECOBIN_MCU_WORK_PREPARATION_H
#include "mcu_control_endpoint.h"
#include "mcu_configuration.h"
#include "mcu_weight_run.h"
#include "mcu_process_measurement.h"
#include "mcu_device_entry_url.h"

/* Configuration/START/measurement owner: mechanical execution is attached
 * explicitly at boot; no legacy fallback or automatic cloud work release.
 * Explicit guard MUST check remaining whole-application prerequisites (actual
 * configuration application, safety, capabilities, authorization, other work).
 * NONE is not a default permission. This owner additionally checks its own
 * config/work/measurement state. Guard is non-mutating and may not reenter.
 * Guard owns CONFIG/START prerequisites; local execution is under that same
 * accepted START and does not request new cloud/per-action authorization.
 * Unknown message contexts must reject, never return NONE as a fallback.
 */
typedef uint16_t (*McuPreparationGuard)(uint8_t message, const uint8_t *payload,
    size_t length, uint64_t now_ms, void *context);
typedef void (*McuPreparedActionPoll)(McuControlEndpoint *endpoint, uint64_t now_ms, void *context);
/* Main applies its real remaining consumers (for example smoke monitoring).
 * Return one only when they use this active configuration. No GPIO actions. */
typedef uint8_t (*McuPreparationApplyConfiguration)(const McuConfiguration *configuration, void *context);
typedef struct {
    McuControlCommandHandler handler;
    McuPreparedActionPoll poll;
    void *context;
} McuPreparedActions;
typedef struct {
    McuConfiguration configuration;
    McuWeightRun weight;
    uint8_t fullness_enabled;
    McuResultMeasurement initial;
    McuProcessMeasurementMeta initial_meta;
    /* Standalone baseline task: it borrows the one scale owner but never enters
     * delivery/clean work state. Exact scope/result survive until Pi SAVED. */
    McuResultMeasurement baseline;
    McuProcessMeasurementMeta baseline_meta;
    uint8_t baseline_scope[MCU_PROCESS_EVENT_SCOPE_LENGTH];
    uint64_t accepted_at_ms;
    uint8_t start_payload[ECOBIN_UART_START_DELIVERY_SESSION_PAYLOAD_MAX_LENGTH];
    uint8_t scratch[ECOBIN_UART_MAX_PAYLOAD_LENGTH];
    McuPreparationGuard guard;
    void *guard_context;
    McuPreparationApplyConfiguration apply_configuration;
    void *configuration_context;
    McuPreparedActions actions[2]; /* Fixed DELIVERY/CLEAN slots, not a dynamic registry. */
    McuPreparedActions recovery; /* Explicit standalone recovery close owner. */
    McuDeviceEntryUrl *device_entry_url; /* Optional boot-local HMI URL owner. */
    uint8_t port_count;
    uint8_t initial_ready;
    uint8_t start_message;
    uint8_t start_length;
    uint8_t recovery_active;
    uint8_t baseline_active;
    uint8_t baseline_published;
    /* Defensive terminal path for an internal invariant violation after the
     * command decision was durably accepted. Normal policy failures are
     * rejected before acceptance and never set this flag. */
    uint8_t baseline_begin_failed;
    uint32_t baseline_first_attempt_sequence;
} McuWorkPreparation;

uint8_t McuWorkPreparation_Attach(McuWorkPreparation *owner, McuControlEndpoint *endpoint,
    uint8_t port_count, McuPreparationGuard guard, void *context);
uint8_t McuWorkPreparation_SetConfigurationApply(McuWorkPreparation *owner, McuControlEndpoint *endpoint,
    McuPreparationApplyConfiguration apply, void *context);
/* Explicit boot-only attachment after real ultrasonic source initialization.
 * It only enables the independent environment-fact poller in main. Delivery,
 * clean and weight acquisition never start, reserve or wait for ranging. */
uint8_t McuWorkPreparation_AttachFullness(McuWorkPreparation *owner, McuControlEndpoint *endpoint);
uint8_t McuWorkPreparation_AttachDeviceEntryUrl(McuWorkPreparation *owner,
    McuControlEndpoint *endpoint, McuDeviceEntryUrl *state,
    McuDeviceEntryUrlWriter writer, void *context);
/* Optional native executors, ONCE per DELIVERY/CLEAN type before binding.
 * Preparation remains the sole endpoint owner. Each handler must return zero
 * for messages it does not own and verify the original work before mutation.
 * No runtime replacement, legacy fallback or concurrent work ownership. */
uint8_t McuWorkPreparation_AttachActions(McuWorkPreparation *owner, McuControlEndpoint *endpoint,
    uint8_t work_type, McuControlCommandHandler handler, McuPreparedActionPoll poll, void *context);
/* One boot-only recovery attachment; no normal work or configuration may start
 * while its result is awaiting exact custody. It owns SAFE_CLOSE only. */
uint8_t McuWorkPreparation_AttachRecovery(McuWorkPreparation *owner, McuControlEndpoint *endpoint,
    McuControlCommandHandler handler, McuPreparedActionPoll poll, void *context);
/* Original validated START and local first acceptance time, not permission to
 * replay or extend a window. External non-overlapping output. No partial copy. */
size_t McuWorkPreparation_CopyStart(const McuWorkPreparation *owner, uint8_t *output,
    size_t capacity, uint8_t *message, uint64_t *accepted_at_ms);
/* Shared foreground measurement operation for an already started
 * work phase: 0 blocked/invalid, 1 pending observation, 2 exact terminal retained
 * and local weight buffer retired. Does not advance phase or imply Pi SAVED.
 * Process mailbox publication is best effort and never gates local progress.
 * measurement/meta are the caller's zero-initialized persistent phase records,
 * not temporary scratch; they survive publication retries and final assembly.
 * Only these records/scratch may be written; no recursive action polling. */
uint8_t McuWorkPreparation_PollMeasurement(McuWorkPreparation *owner, McuControlEndpoint *endpoint,
    uint64_t now_ms, uint8_t message, uint16_t step, McuResultMeasurement *measurement,
    McuProcessMeasurementMeta *meta);
/* Called only after the action owner validates the original interrupted work.
 * Stop the unfinished business weight acquisition. Independent environment
 * observations are neither started nor changed by this business operation. */
uint8_t McuWorkPreparation_InterruptMeasurement(McuWorkPreparation *owner, uint64_t now_ms);
/* Foreground only, between Feed calls. Ingress must first drain genuinely owned
 * captured frames through owner->weight; local IDs do not prove RS485 ownership.
 */
uint8_t McuWorkPreparation_Poll(McuWorkPreparation *owner, McuControlEndpoint *endpoint, uint64_t now_ms);
#endif
