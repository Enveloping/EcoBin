#ifndef ECOBIN_MCU_PROCESS_EVENT_SLOT_H
#define ECOBIN_MCU_PROCESS_EVENT_SLOT_H
#include "../../contracts/uart/generated/c/ecobin_uart_protocol.h"

/* Candidate, single foreground owner. No allocator/Flash/GPIO/business writes.
 * Caller supplies the accepted original command/work scope, not a query digest.
 * Init only for actual MCU reset/binding, never for a Pi reconnect.
 * One immutable held event: the owner must not advance to another process event
 * until this handoff completes. Saved releases DATA only, never work/admission.
 * Caller allocates globally unique, strictly increasing event numbers per boot.
 * A DELIVERY_SELECTION may replace only an exactly saved available post-close
 * weight from the same original work/round/config and the stated measurement.
 * This custody guard neither creates user intent nor proves local motion safety.
 */
#define MCU_PROCESS_EVENT_SCOPE_LENGTH (ECOBIN_UART_QUERY_PROCESS_EVENT_PAYLOAD_MAX_LENGTH - 8u)
#define MCU_PROCESS_EVENT_BODY_CAPACITY ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_PAYLOAD_MAX_LENGTH
typedef struct {
    uint64_t boot_id;
    uint32_t highest_sequence;
    uint8_t scope[MCU_PROCESS_EVENT_SCOPE_LENGTH];
    uint8_t payload[MCU_PROCESS_EVENT_BODY_CAPACITY];
    uint8_t identity[ECOBIN_UART_PROCESS_EVENT_SAVED_PAYLOAD_MAX_LENGTH];
    uint16_t length;
    uint8_t held;
} McuProcessEventSlot;

void McuProcessEventSlot_Init(McuProcessEventSlot *slot, uint64_t boot_id);
uint8_t McuProcessEventSlot_Freeze(McuProcessEventSlot *slot, const uint8_t *scope,
    size_t scope_length, uint8_t message_type, const uint8_t *payload, size_t length);
size_t McuProcessEventSlot_CopyHeld(const McuProcessEventSlot *slot, uint8_t *output, size_t capacity);
/* Echoes the complete valid request. NOT_FOUND is not evidence of nonexecution.
 * Most recent released identity remains queryable until another event replaces
 * it. The work owner, not this one-slot cache, owns monotonic business steps. */
size_t McuProcessEventSlot_Query(const McuProcessEventSlot *slot, const uint8_t *request,
    size_t length, uint8_t *reply, size_t capacity);
uint8_t McuProcessEventSlot_Saved(McuProcessEventSlot *slot, const uint8_t *identity, size_t length);
#endif
