#ifndef ECOBIN_MCU_CLEAN_EXECUTION_H
#define ECOBIN_MCU_CLEAN_EXECUTION_H
#include "mcu_opening_gate.h"

/* Accepted START owns local first unlock and subsequent HMI requests. The
 * pulse is timer-owned. FINISH is the cleaner's manual-close confirmation;
 * final weighing then freezes the result without any Pi process ACK. */
typedef struct {
    McuWorkPreparation *preparation;
    uint8_t start_uid[16];
    uint64_t operation_deadline_ms, last_now_ms, intent_at_ms;
    McuResultMeasurement final_measurement;
    McuProcessMeasurementMeta final_meta;
    uint32_t pulse_token, unlock_pulse_ms;
    uint16_t action_sequence;
    uint8_t active, started, final_state, physical_close_confirmed;
} McuCleanExecution;
uint8_t McuCleanExecution_Attach(McuCleanExecution *owner,
    McuWorkPreparation *preparation, McuControlEndpoint *endpoint);
/* Foreground HMI input scoped to the displayed original work/action sequence.
 * UNLOCK_REQUESTED reopens within CLEAN_ACTIVE. FINISH_REQUESTED confirms
 * manual close, starts final weighing, and prevents further old-page actions. */
uint8_t McuCleanExecution_Request(McuCleanExecution *owner, McuControlEndpoint *endpoint,
    const uint8_t *operation_uid, uint8_t message, uint16_t after_action_sequence, uint64_t now_ms);
/* Historical second-confirmation API is unsupported; do not call from polling. */
uint8_t McuCleanExecution_Confirm(McuCleanExecution *owner, McuControlEndpoint *endpoint,
    const uint8_t *operation_uid, uint16_t action_sequence, const uint8_t *final_measurement_uid, uint64_t now_ms);
#endif
