#ifndef ECOBIN_MCU_ACTUATOR_EVENT_JOURNAL_H
#define ECOBIN_MCU_ACTUATOR_EVENT_JOURNAL_H
#include "../../contracts/uart/generated/c/ecobin_uart_protocol.h"

/* Native candidate, one journal per boot, single foreground owner; NOT attached
 * to main. The control endpoint owns the shared instance and number credits.
 * Only immutable output/interruption evidence. No GPIO, timer, Flash, allocator or transport.
 * Reserve every expected edge BEFORE motion (e.g. both lock ON and OFF).
 * This low-level Reserve covers RAM only, NOT authorization or number credit.
 * Attached producers use ONLY McuControlEndpoint actuator APIs for both budgets.
 * Caller must still prove command/work ownership, reserve global sequence budget,
 * and preserve independent deenergization/close scheduling before any execution.
 * Init only on real MCU reset/new binding; never on Pi reconnect or lost receipt.
 * All caller input/output buffers must be outside the journal object.
 */
#define MCU_ACTUATOR_EVENT_CAPACITY 8u
#define MCU_ACTUATOR_EVENT_BODY_CAPACITY ECOBIN_UART_DELIVERY_LOCAL_DOOR_RESULT_PAYLOAD_MAX_LENGTH

typedef struct {
    uint64_t boot_id;
    uint32_t number;
} McuActuatorEventReservation;

typedef struct {
    uint32_t reservation;
    uint16_t length;
    uint8_t member;
    uint8_t state;
    uint8_t started;
    uint8_t message_type;
    uint8_t payload[MCU_ACTUATOR_EVENT_BODY_CAPACITY];
} McuActuatorEventEntry;

typedef struct {
    uint64_t boot_id;
    uint32_t last_reservation;
    uint32_t highest_sequence;
    McuActuatorEventEntry entries[MCU_ACTUATOR_EVENT_CAPACITY];
} McuActuatorEventJournal;

void McuActuatorEventJournal_Init(McuActuatorEventJournal *journal, uint64_t boot_id);
/* Each unfilled reserved slot owes exactly one future global event number. */
uint8_t McuActuatorEventJournal_PendingCount(const McuActuatorEventJournal *journal);
/* Exact retained member already SAVED; false for absent/reclaimed/reserved/held
 * or wrong-type data. Read-only custody proof, never action/work permission. */
uint8_t McuActuatorEventJournal_MemberSaved(const McuActuatorEventJournal *journal,
    const McuActuatorEventReservation *reservation, uint8_t member, uint8_t message_type);
/* Atomic all-or-none capacity reservation. Token never reused in this boot.
 * Prefer unused capacity before reclaiming already-saved evidence identities.
 * Failure leaves both journal and output token unchanged. */
uint8_t McuActuatorEventJournal_Reserve(McuActuatorEventJournal *journal,
    uint8_t count, McuActuatorEventReservation *reservation);
/* Cancel capacity only before ANY member was published. Once started, even a
 * saved ON must not cancel the still-reserved OFF. No automatic timeout/eviction.
 * Caller must determine no motion started before invoking cancellation. */
uint8_t McuActuatorEventJournal_Cancel(McuActuatorEventJournal *journal,
    const McuActuatorEventReservation *reservation);
/* Original global boot/event sequence belongs to the producer. New publication
 * must advance this journal's high-water mark. Exact retry is idempotent and
 * never turns released data back into pending data. No business deduplication. */
uint8_t McuActuatorEventJournal_Freeze(McuActuatorEventJournal *journal,
    const McuActuatorEventReservation *reservation, uint8_t member,
    uint8_t message_type, const uint8_t *payload, size_t length);
/* Endpoint-owned publication: template has sequence=0 (local only, invalid on
 * wire), all other fields final. Fill the next shared number, validate/freeze,
 * then advance the counter. No mutation on failure. Exact duplicate returns the
 * retained number without consuming credit or resurrecting saved data. Returns
 * 0 for invalid/stale/conflicting/reclaimed members. shared_sequence must be the
 * one global boot-local allocator, never a producer-local counter. */
uint32_t McuActuatorEventJournal_PublishNext(McuActuatorEventJournal *journal,
    const McuActuatorEventReservation *reservation, uint8_t member,
    uint8_t message_type, const uint8_t *payload_template, size_t length,
    uint32_t *shared_sequence);
/* Read-only oldest HELD event after the cursor. 0 means no matching retained
 * data or bad output arguments, NEVER proof that no motion occurred. */
size_t McuActuatorEventJournal_CopyNextHeld(const McuActuatorEventJournal *journal,
    uint32_t after_sequence, uint8_t *message_type, uint8_t *output, size_t capacity);
/* LOCAL API, not an automatic ACK. Caller may invoke only with exact original
 * data proven durably saved. Wire callers instead use the identity-checked Saved.
 * Returns existing ResultSavedStatus (0=invalid). Releases DATA only; never a
 * pending reservation, command, work, physical output or admission lock.
 * Released identity/body remain idempotent until capacity is reused, then an
 * old confirmation returns NOT_FOUND, not a guessed release/nonexecution fact.
 */
uint8_t McuActuatorEventJournal_ConfirmSaved(McuActuatorEventJournal *journal,
    uint8_t message_type, const uint8_t *payload, size_t length);
/* Wire custody: fresh read-only query metadata; HELD refers to CopyNextHeld.
 * Saved identity is boot/sequence/type/domain-separated full body SHA-256.
 * Neither operation changes reservations, work or physical output. */
size_t McuActuatorEventJournal_Query(const McuActuatorEventJournal *journal,
    const uint8_t *request, size_t length, uint8_t *reply, size_t capacity);
uint8_t McuActuatorEventJournal_Saved(McuActuatorEventJournal *journal,
    const uint8_t *identity, size_t length);
#endif
