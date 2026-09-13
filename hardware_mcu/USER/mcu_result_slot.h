#ifndef ECOBIN_MCU_RESULT_SLOT_H
#define ECOBIN_MCU_RESULT_SLOT_H

#include <stddef.h>
#include <stdint.h>
#include "../../contracts/uart/generated/c/ecobin_uart_protocol.h"

/* Native UART 2 candidate, foreground-only. Not connected to main/Keil/UART.
 * Owns one immutable, fully validated result. No heap, flash, GPIO or clocks.
 * Init only after binding / on MCU reset, NEVER on an edge reconnect.
 * All input/output buffers must be external and must not overlap this struct.
 */
typedef struct {
    uint64_t boot_id;
    uint32_t highest_sequence;
    uint8_t payload[ECOBIN_UART_WORK_RESULT_PAYLOAD_MAX_LENGTH];
    uint8_t held;
} McuResultSlot;

void McuResultSlot_Init(McuResultSlot *slot, uint64_t boot_id);
/* 1: newly frozen or exact duplicate of still-held result. 0: no change.
 * Sequence never wraps/reuses within a boot. The business owner must stop
 * creating results while held; release does not itself authorize new work.
 */
uint8_t McuResultSlot_Freeze(McuResultSlot *slot, const uint8_t *payload, size_t length);
size_t McuResultSlot_CopyHeld(const McuResultSlot *slot, uint8_t *output, size_t capacity);
/* The identity is exactly the RESULT_SAVED 60-byte payload, not a frame ACK.
 * Return generated status, or 0 for malformed identity (send no reply then).
 * Query callers echo queryId themselves and resend CopyHeld only for HELD.
 * Only the latest result identity is retained after release; older identities
 * may return NOT_FOUND. That never implies no past execution or no Pi record.
 */
uint8_t McuResultSlot_Query(const McuResultSlot *slot, const uint8_t *identity, size_t length);
uint8_t McuResultSlot_Saved(McuResultSlot *slot, const uint8_t *identity, size_t length);

#endif
