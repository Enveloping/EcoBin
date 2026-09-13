#ifndef ECOBIN_MCU_CLEAN_EXECUTION_H
#define ECOBIN_MCU_CLEAN_EXECUTION_H
#include "mcu_opening_gate.h"

/* Native first/repeated unlock, result custody and preunlock weight failure, not a complete
 * clean/HMI/recovery workflow.
 * Original configuration/initial SAVED and grant are checked before acceptance;
 * ON/OFF plus an independent interruption record and number are reserved before
 * first energizing. Reopens retain the original interruption reservation.
 * Timer cuts power independently. Foreground retains both actual edge times,
 * never implies coil health/door position, and does not release Pi occupancy.
 * Original work/config/action-scoped button intents require exact Pi custody.
 * A new unlock grant consumes only the saved reopen intent; repeated grants
 * never replay power. FINISH starts a distinct weight after exact intent SAVED.
 * Reopening after the saved final weight invalidates that candidate, not its
 * archived bytes. Fresh human close/candidate confirmation is separately held;
 * exact Pi custody then freezes WORK_RESULT from the original measurements.
 * Confirmation never makes an unavailable/interrupted measurement valid.
 * An unavailable/interrupted initial attempt never unlocks; exact custody ends
 * that work as FAILED with no final measurement or human close confirmation.
 * Update stop, operation expiry, lost control context and accepted-but-rejected
 * dispatch retain the original cause and actual edges/final attempt. Exact Pi
 * custody leaves CLEAN_RECOVERY_REQUIRED; never invents manual door closure.
 * Missing pulse evidence stays missing. Normal duration is timer-owned; expiry
 * observation aborts only our exact retained pulse, not another action/update.
 * Normal confirmed completion returns the unused interruption reservation.
 * Recovery execution, classification and admission require subsequent handlers.
 * Not connected to main or the legacy HMI/serial path. */
typedef struct {
    McuWorkPreparation *preparation;
    McuActuatorEventReservation records;
    McuActuatorEventReservation interruption_record;
    uint8_t grant[ECOBIN_UART_UNLOCK_CLEAN_DOOR_PAYLOAD_MAX_LENGTH];
    uint64_t operation_deadline_ms;
    uint32_t pulse_token;
    uint32_t intent_event_sequence;
    uint64_t intent_at_ms;
    uint32_t confirmation_event_sequence;
    uint64_t confirmation_at_ms;
    uint64_t interrupted_at_ms;
    McuResultMeasurement final_measurement;
    McuProcessMeasurementMeta final_meta;
    uint16_t action_sequence;
    uint8_t intent_message;
    uint8_t final_state; /* 0 not started, 1 measuring, 2 immutable candidate. */
    uint8_t active;
    uint8_t on_published;
    uint8_t off_published;
    uint8_t interruption_reason;
    uint8_t interrupted_phase;
    uint8_t interruption_published;
} McuCleanExecution;

uint8_t McuCleanExecution_Attach(McuCleanExecution *owner,
    McuWorkPreparation *preparation, McuControlEndpoint *endpoint);
/* Foreground HMI boundary; original displayed operation/last action sequence,
 * never a delayed button relabelled with the latest state. Only UNLOCK_REQUESTED
 * or FINISH_REQUESTED. Records intent, does not unlock or confirm physical close.
 * Caller serializes with Feed/Poll, and must obtain the return before updating UI. */
uint8_t McuCleanExecution_Request(McuCleanExecution *owner, McuControlEndpoint *endpoint,
    const uint8_t *operation_uid, uint8_t message, uint16_t after_action_sequence, uint64_t now_ms);
/* Fresh explicit cleaner confirmation of the displayed candidate AND manual
 * close/installation. Never call this from polling, power state or a timer.
 * Original operation, finish generation and measurement UUID must all match.
 * Retains the human fact before final-result custody; does not release Pi work. */
uint8_t McuCleanExecution_Confirm(McuCleanExecution *owner, McuControlEndpoint *endpoint,
    const uint8_t *operation_uid, uint16_t action_sequence, const uint8_t *final_measurement_uid, uint64_t now_ms);
#endif
