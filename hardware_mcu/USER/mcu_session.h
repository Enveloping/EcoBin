#ifndef ECOBIN_MCU_SESSION_H
#define ECOBIN_MCU_SESSION_H

#include <stdint.h>

/* Native UART 2 candidate, not connected to main.c or the legacy transport.
 * Foreground-only, single owner. No allocation, flash, timer or GPIO access.
 * Wire enum values come exclusively from the generated candidate contract.
 * Do not serialize these structs: C padding is not the wire representation.
 */
typedef struct {
    uint64_t target_boot_id;
    uint32_t sequence;
    uint8_t uid[16];
    uint8_t digest[32];
} McuSessionCommand;

typedef struct {
    uint64_t boot_id;
    uint64_t pending_probe_id;
    McuSessionCommand latest_command;
    uint32_t highest_sequence;
    uint16_t latest_error;
} McuSession;

typedef struct {
    uint64_t current_boot_id;
    uint8_t status;
} McuSessionBindingReply;

typedef struct {
    McuSessionCommand command;
    uint64_t current_boot_id;
    uint32_t highest_sequence;
    uint16_t error_code;
    uint8_t outcome;
    uint8_t execute_once;
} McuSessionDecision;

void McuSession_Init(McuSession *session);
/* Return 0 for an invalid identity; no state change and no wire reply then. */
uint8_t McuSession_Probe(McuSession *session, uint64_t probe_id,
                         uint64_t *current_boot_id);
uint8_t McuSession_Bind(McuSession *session, uint64_t probe_id,
                        uint64_t proposed_boot_id, McuSessionBindingReply *reply);

/* Caller MUST validate the complete wire payload, digest and non-mutating
 * business preconditions before this call. business_error is NackError or NONE.
 * State is cached before returning execute_once=1; only then may the caller
 * dispatch that action once. Acceptance is not proof of physical completion.
 * No retransmission is requested here. Invalid input returns 0, leaves session
 * unchanged and clears execute_once; other reply fields must not be consumed.
 * Input and output buffers must not overlap.
 */
uint8_t McuSession_ReceiveCommand(McuSession *session,
    const McuSessionCommand *command, uint16_t business_error,
    McuSessionDecision *decision);

/* The caller echoes queryId separately. This never changes the command
 * high-water mark, discards work/results or authorizes a mechanical action. */
uint8_t McuSession_QueryCommand(const McuSession *session,
    const McuSessionCommand *command, McuSessionDecision *decision);

#endif
