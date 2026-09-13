#ifndef ECOBIN_MCU_WORK_STATE_H
#define ECOBIN_MCU_WORK_STATE_H

#include "mcu_result_slot.h"

#define MCU_WORK_IDENTITY_LENGTH (ECOBIN_UART_QUERY_WORK_PAYLOAD_MAX_LENGTH - ECOBIN_UART_QUERY_WORK_MCU_COMMAND_UID_OFFSET)

/* Candidate, foreground-only, external non-overlapping buffers. No GPIO,
 * serial owner, Flash or clock. Query captures one coherent work observation;
 * it is NOT a device-wide physical snapshot or permission to retry an action.
 * Init only on MCU reset/binding, never on Pi reconnect.
 */
typedef struct {
    McuResultSlot result;
    uint8_t identity[MCU_WORK_IDENTITY_LENGTH];
    uint8_t status;
    uint8_t phase;
} McuWorkState;

void McuWorkState_Init(McuWorkState *state, uint64_t boot_id);
/* Pure preflight of BeginAccepted's retained-work constraints, before caching
 * a command decision. Does not authorize a command or replace session guards. */
uint8_t McuWorkState_CanBegin(const McuWorkState *state, const uint8_t *identity, size_t length, uint8_t phase);
/* Caller MUST first fully validate/accept START or RESUME through McuSession.
 * identity: QUERY_WORK payload excluding queryId (original command + work).
 * This records accepted work; it does not authorize or perform any motion.
 * Retains only the last work, rejects replacement until RESULT_SAVED.
 */
uint8_t McuWorkState_BeginAccepted(McuWorkState *state, const uint8_t *identity, size_t length, uint8_t phase);
/* Reports phase from the owning state machine, not a transition/admission engine. */
uint8_t McuWorkState_SetPhase(McuWorkState *state, uint8_t phase);
uint8_t McuWorkState_Complete(McuWorkState *state, const uint8_t *payload, size_t length);
uint8_t McuWorkState_Saved(McuWorkState *state, const uint8_t *identity, size_t length);
size_t McuWorkState_CopyHeld(const McuWorkState *state, uint8_t *output, size_t capacity);
/* Input/output are complete QUERY_WORK / WORK_QUERY_REPLY payloads. 0 invalid.
 * Unavailable work returns an empty phase, NOT proof the whole device is idle.
 * No response status clears Pi business occupancy or authorizes new business.
 */
size_t McuWorkState_Query(const McuWorkState *state, const uint8_t *request, size_t length,
                         uint8_t *output, size_t capacity);

#endif
