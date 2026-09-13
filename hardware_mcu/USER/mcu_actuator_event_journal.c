#include "mcu_actuator_event_journal.h"
#include <string.h>

typedef char actuator_journal_ram_budget[(sizeof(McuActuatorEventJournal) <= 640u) ? 1 : -1];
typedef char actuator_body_lock_fits[(ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_PAYLOAD_MAX_LENGTH <= MCU_ACTUATOR_EVENT_BODY_CAPACITY) ? 1 : -1];
typedef char actuator_body_abort_fits[(ECOBIN_UART_DELIVERY_CYCLE_ABORTED_PAYLOAD_MAX_LENGTH <= MCU_ACTUATOR_EVENT_BODY_CAPACITY) ? 1 : -1];
typedef char actuator_body_postclose_fits[(ECOBIN_UART_DELIVERY_POSTCLOSE_INTERRUPTED_PAYLOAD_MAX_LENGTH <= MCU_ACTUATOR_EVENT_BODY_CAPACITY) ? 1 : -1];
typedef char actuator_body_clean_interrupt_fits[(ECOBIN_UART_CLEAN_OPERATION_INTERRUPTED_PAYLOAD_MAX_LENGTH <= MCU_ACTUATOR_EVENT_BODY_CAPACITY) ? 1 : -1];
typedef char actuator_body_safe_close_fits[(ECOBIN_UART_SAFE_CLOSE_RESULT_PAYLOAD_MAX_LENGTH <= MCU_ACTUATOR_EVENT_BODY_CAPACITY) ? 1 : -1];
typedef char actuator_event_sequence_offsets_match[(
    ECOBIN_UART_DELIVERY_DOOR_COMMAND_RESULT_MCU_EVENT_SEQUENCE_OFFSET == ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET
    && ECOBIN_UART_SAFE_CLOSE_RESULT_MCU_EVENT_SEQUENCE_OFFSET == ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET) ? 1 : -1];

enum { ENTRY_FREE = 0, ENTRY_RESERVED = 1, ENTRY_HELD = 2, ENTRY_RELEASED = 3 };

static McuActuatorEventEntry *find_member(McuActuatorEventJournal *journal, uint32_t reservation, uint8_t member) {
    size_t i;
    for (i = 0u; i < MCU_ACTUATOR_EVENT_CAPACITY; ++i)
        if (journal->entries[i].state != ENTRY_FREE
            && journal->entries[i].reservation == reservation && journal->entries[i].member == member)
            return &journal->entries[i];
    return NULL;
}

static uint8_t valid_body(uint8_t message_type, const uint8_t *payload, size_t length) {
    if (payload == NULL || length > MCU_ACTUATOR_EVENT_BODY_CAPACITY) return 0u;
    switch (message_type) {
    case ECOBIN_UART_MESSAGE_DELIVERY_LOCAL_DOOR_RESULT:
    case ECOBIN_UART_MESSAGE_DELIVERY_CYCLE_ABORTED:
    case ECOBIN_UART_MESSAGE_DELIVERY_POSTCLOSE_INTERRUPTED:
    case ECOBIN_UART_MESSAGE_DELIVERY_DOOR_COMMAND_RESULT:
    case ECOBIN_UART_MESSAGE_SAFE_CLOSE_RESULT:
    case ECOBIN_UART_MESSAGE_CLEAN_LOCK_POWER_CHANGED:
    case ECOBIN_UART_MESSAGE_CLEAN_OPERATION_INTERRUPTED:
        return (uint8_t)(ecobin_uart_validate_session_payload(message_type, payload, (uint16_t)length) == 0);
    default: return 0u;
    }
}

void McuActuatorEventJournal_Init(McuActuatorEventJournal *journal, uint64_t boot_id) {
    if (journal == NULL) return;
    memset(journal, 0, sizeof(*journal));
    if (boot_id <= UINT64_C(9007199254740991)) journal->boot_id = boot_id;
}

uint8_t McuActuatorEventJournal_PendingCount(const McuActuatorEventJournal *journal) {
    size_t i;
    uint8_t pending = 0u;
    if (journal == NULL) return 0u;
    for (i = 0u; i < MCU_ACTUATOR_EVENT_CAPACITY; ++i)
        if (journal->entries[i].state == ENTRY_RESERVED) ++pending;
    return pending;
}

uint8_t McuActuatorEventJournal_MemberSaved(const McuActuatorEventJournal *journal,
    const McuActuatorEventReservation *reservation, uint8_t member, uint8_t message_type) {
    size_t i;
    if (journal == NULL || reservation == NULL || journal->boot_id == 0u
        || reservation->boot_id != journal->boot_id || reservation->number == 0u) return 0u;
    for (i = 0u; i < MCU_ACTUATOR_EVENT_CAPACITY; ++i) {
        const McuActuatorEventEntry *entry = &journal->entries[i];
        if (entry->reservation == reservation->number && entry->member == member)
            return (uint8_t)(entry->state == ENTRY_RELEASED && entry->message_type == message_type);
    }
    return 0u;
}

uint8_t McuActuatorEventJournal_Reserve(McuActuatorEventJournal *journal,
    uint8_t count, McuActuatorEventReservation *reservation) {
    size_t i;
    uint8_t free_count = 0u, member = 0u, pass;
    if (journal == NULL || reservation == NULL || journal->boot_id == 0u
        || count == 0u || count > MCU_ACTUATOR_EVENT_CAPACITY
        || journal->last_reservation == UINT32_MAX || journal->highest_sequence == UINT32_MAX) return 0u;
    for (i = 0u; i < MCU_ACTUATOR_EVENT_CAPACITY; ++i)
        if (journal->entries[i].state == ENTRY_FREE || journal->entries[i].state == ENTRY_RELEASED) ++free_count;
    if (free_count < count) return 0u;
    ++journal->last_reservation;
    for (pass = 0u; pass < 2u; ++pass)
        for (i = 0u; i < MCU_ACTUATOR_EVENT_CAPACITY && member < count; ++i) {
            McuActuatorEventEntry *entry = &journal->entries[i];
            if (entry->state != (pass == 0u ? ENTRY_FREE : ENTRY_RELEASED)) continue;
            memset(entry, 0, sizeof(*entry));
            entry->reservation = journal->last_reservation;
            entry->member = member++;
            entry->state = ENTRY_RESERVED;
        }
    reservation->boot_id = journal->boot_id;
    reservation->number = journal->last_reservation;
    return 1u;
}

uint8_t McuActuatorEventJournal_Freeze(McuActuatorEventJournal *journal,
    const McuActuatorEventReservation *reservation, uint8_t member,
    uint8_t message_type, const uint8_t *payload, size_t length) {
    size_t i;
    McuActuatorEventEntry *entry;
    uint32_t sequence;
    if (journal == NULL || reservation == NULL || journal->boot_id == 0u
        || reservation->boot_id != journal->boot_id || reservation->number == 0u
        || !valid_body(message_type, payload, length)
        || ecobin_uart_read_u64_be(payload) != journal->boot_id) return 0u;
    entry = find_member(journal, reservation->number, member);
    if (entry == NULL) return 0u;
    if (entry->state != ENTRY_RESERVED)
        return (uint8_t)(entry->message_type == message_type && entry->length == length
            && memcmp(entry->payload, payload, length) == 0);
    sequence = ecobin_uart_read_u32_be(payload + ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET);
    if (sequence <= journal->highest_sequence) return 0u;
    memcpy(entry->payload, payload, length);
    entry->length = (uint16_t)length;
    entry->message_type = message_type;
    entry->state = ENTRY_HELD;
    journal->highest_sequence = sequence;
    for (i = 0u; i < MCU_ACTUATOR_EVENT_CAPACITY; ++i)
        if (journal->entries[i].reservation == reservation->number) journal->entries[i].started = 1u;
    return 1u;
}

uint8_t McuActuatorEventJournal_Cancel(McuActuatorEventJournal *journal,
    const McuActuatorEventReservation *reservation) {
    size_t i;
    uint8_t found = 0u;
    if (journal == NULL || reservation == NULL || journal->boot_id == 0u
        || reservation->boot_id != journal->boot_id || reservation->number == 0u) return 0u;
    for (i = 0u; i < MCU_ACTUATOR_EVENT_CAPACITY; ++i) {
        const McuActuatorEventEntry *entry = &journal->entries[i];
        if (entry->state == ENTRY_FREE || entry->reservation != reservation->number) continue;
        if (entry->state != ENTRY_RESERVED || entry->started) return 0u;
        found = 1u;
    }
    if (!found) return 0u;
    for (i = 0u; i < MCU_ACTUATOR_EVENT_CAPACITY; ++i)
        if (journal->entries[i].reservation == reservation->number)
            memset(&journal->entries[i], 0, sizeof(journal->entries[i]));
    return 1u;
}

uint32_t McuActuatorEventJournal_PublishNext(McuActuatorEventJournal *journal,
    const McuActuatorEventReservation *reservation, uint8_t member,
    uint8_t message_type, const uint8_t *payload_template, size_t length,
    uint32_t *shared_sequence) {
    uint8_t body[MCU_ACTUATOR_EVENT_BODY_CAPACITY];
    McuActuatorEventEntry *entry;
    uint8_t is_new;
    uint32_t sequence;
    if (journal == NULL || reservation == NULL || shared_sequence == NULL || payload_template == NULL
        || journal->boot_id == 0u || reservation->boot_id != journal->boot_id || reservation->number == 0u
        || length < ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET + 4u
        || length > sizeof(body)
        || ecobin_uart_read_u32_be(payload_template + ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET) != 0u) return 0u;
    entry = find_member(journal, reservation->number, member);
    if (entry == NULL) return 0u;
    is_new = (uint8_t)(entry->state == ENTRY_RESERVED);
    if (is_new) {
        if (*shared_sequence == UINT32_MAX || *shared_sequence < journal->highest_sequence) return 0u;
        sequence = *shared_sequence + 1u;
    } else {
        sequence = ecobin_uart_read_u32_be(entry->payload + ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET);
    }
    memcpy(body, payload_template, length);
    ecobin_uart_write_u32_be(body + ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET, sequence);
    if (!McuActuatorEventJournal_Freeze(journal, reservation, member, message_type, body, length)) return 0u;
    if (is_new) *shared_sequence = sequence;
    return sequence;
}

size_t McuActuatorEventJournal_CopyNextHeld(const McuActuatorEventJournal *journal,
    uint32_t after_sequence, uint8_t *message_type, uint8_t *output, size_t capacity) {
    size_t i;
    const McuActuatorEventEntry *next = NULL;
    uint32_t next_sequence = 0u;
    if (journal == NULL || message_type == NULL || output == NULL) return 0u;
    for (i = 0u; i < MCU_ACTUATOR_EVENT_CAPACITY; ++i) {
        const McuActuatorEventEntry *entry = &journal->entries[i];
        uint32_t sequence;
        if (entry->state != ENTRY_HELD) continue;
        sequence = ecobin_uart_read_u32_be(entry->payload + ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET);
        if (sequence > after_sequence && (next == NULL || sequence < next_sequence)) {
            next = entry;
            next_sequence = sequence;
        }
    }
    if (next == NULL || capacity < next->length) return 0u;
    memcpy(output, next->payload, next->length);
    *message_type = next->message_type;
    return next->length;
}

uint8_t McuActuatorEventJournal_ConfirmSaved(McuActuatorEventJournal *journal,
    uint8_t message_type, const uint8_t *payload, size_t length) {
    size_t i;
    uint32_t sequence;
    if (journal == NULL || !valid_body(message_type, payload, length)) return 0u;
    if (ecobin_uart_read_u64_be(payload) != journal->boot_id)
        return ECOBIN_UART_RESULT_SAVED_STATUS_BOOT_MISMATCH;
    sequence = ecobin_uart_read_u32_be(payload + ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET);
    for (i = 0u; i < MCU_ACTUATOR_EVENT_CAPACITY; ++i) {
        McuActuatorEventEntry *entry = &journal->entries[i];
        if ((entry->state != ENTRY_HELD && entry->state != ENTRY_RELEASED)
            || ecobin_uart_read_u32_be(entry->payload + ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET) != sequence) continue;
        if (entry->message_type != message_type || entry->length != length || memcmp(entry->payload, payload, length) != 0)
            return ECOBIN_UART_RESULT_SAVED_STATUS_IDENTITY_CONFLICT;
        if (entry->state == ENTRY_RELEASED) return ECOBIN_UART_RESULT_SAVED_STATUS_ALREADY_RELEASED;
        entry->state = ENTRY_RELEASED;
        return ECOBIN_UART_RESULT_SAVED_STATUS_RELEASED;
    }
    return ECOBIN_UART_RESULT_SAVED_STATUS_NOT_FOUND;
}

size_t McuActuatorEventJournal_Query(const McuActuatorEventJournal *journal,
    const uint8_t *request, size_t length, uint8_t *reply, size_t capacity) {
    uint8_t body[MCU_ACTUATOR_EVENT_BODY_CAPACITY], message = 0u;
    size_t held_length = 0u;
    uint8_t status;
    if (journal == NULL || request == NULL || reply == NULL
        || length != ECOBIN_UART_QUERY_ACTUATOR_EVENT_PAYLOAD_MAX_LENGTH
        || capacity < ECOBIN_UART_ACTUATOR_EVENT_QUERY_REPLY_PAYLOAD_MAX_LENGTH
        || ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_QUERY_ACTUATOR_EVENT, request, (uint16_t)length) != 0) return 0u;
    if (ecobin_uart_read_u64_be(request + ECOBIN_UART_QUERY_ACTUATOR_EVENT_TARGET_MCU_BOOT_ID_OFFSET) != journal->boot_id)
        status = ECOBIN_UART_ACTUATOR_EVENT_QUERY_STATUS_BOOT_MISMATCH;
    else {
        held_length = McuActuatorEventJournal_CopyNextHeld(journal,
            ecobin_uart_read_u32_be(request + ECOBIN_UART_QUERY_ACTUATOR_EVENT_AFTER_MCU_EVENT_SEQUENCE_OFFSET),
            &message, body, sizeof(body));
        status = held_length ? ECOBIN_UART_ACTUATOR_EVENT_QUERY_STATUS_HELD : ECOBIN_UART_ACTUATOR_EVENT_QUERY_STATUS_NOT_FOUND;
    }
    memmove(reply, request, length);
    memset(reply + length, 0, ECOBIN_UART_ACTUATOR_EVENT_QUERY_REPLY_PAYLOAD_MAX_LENGTH - length);
    ecobin_uart_write_u64_be(reply + ECOBIN_UART_ACTUATOR_EVENT_QUERY_REPLY_CURRENT_MCU_BOOT_ID_OFFSET, journal->boot_id);
    reply[ECOBIN_UART_ACTUATOR_EVENT_QUERY_REPLY_STATUS_OFFSET] = status;
    if (held_length) {
        memcpy(reply + ECOBIN_UART_ACTUATOR_EVENT_QUERY_REPLY_MCU_EVENT_SEQUENCE_OFFSET,
            body + ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET, 4u);
        reply[ECOBIN_UART_ACTUATOR_EVENT_QUERY_REPLY_EVENT_MESSAGE_TYPE_OFFSET] = message;
        ecobin_uart_compute_actuator_event_digest(message, body, (uint16_t)held_length,
            reply + ECOBIN_UART_ACTUATOR_EVENT_QUERY_REPLY_EVENT_DIGEST_SHA256_OFFSET);
    }
    return ECOBIN_UART_ACTUATOR_EVENT_QUERY_REPLY_PAYLOAD_MAX_LENGTH;
}

uint8_t McuActuatorEventJournal_Saved(McuActuatorEventJournal *journal,
    const uint8_t *identity, size_t length) {
    size_t i;
    uint32_t sequence;
    uint8_t digest[32];
    if (journal == NULL || identity == NULL || length != ECOBIN_UART_ACTUATOR_EVENT_SAVED_PAYLOAD_MAX_LENGTH
        || ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_ACTUATOR_EVENT_SAVED, identity, (uint16_t)length) != 0) return 0u;
    if (ecobin_uart_read_u64_be(identity + ECOBIN_UART_ACTUATOR_EVENT_SAVED_MCU_BOOT_ID_OFFSET) != journal->boot_id)
        return ECOBIN_UART_RESULT_SAVED_STATUS_BOOT_MISMATCH;
    sequence = ecobin_uart_read_u32_be(identity + ECOBIN_UART_ACTUATOR_EVENT_SAVED_MCU_EVENT_SEQUENCE_OFFSET);
    for (i = 0u; i < MCU_ACTUATOR_EVENT_CAPACITY; ++i) {
        McuActuatorEventEntry *entry = &journal->entries[i];
        if ((entry->state != ENTRY_HELD && entry->state != ENTRY_RELEASED)
            || ecobin_uart_read_u32_be(entry->payload + ECOBIN_UART_CLEAN_LOCK_POWER_CHANGED_MCU_EVENT_SEQUENCE_OFFSET) != sequence) continue;
        if (entry->message_type != identity[ECOBIN_UART_ACTUATOR_EVENT_SAVED_EVENT_MESSAGE_TYPE_OFFSET])
            return ECOBIN_UART_RESULT_SAVED_STATUS_IDENTITY_CONFLICT;
        ecobin_uart_compute_actuator_event_digest(entry->message_type, entry->payload, entry->length, digest);
        if (memcmp(digest, identity + ECOBIN_UART_ACTUATOR_EVENT_SAVED_EVENT_DIGEST_SHA256_OFFSET, sizeof(digest)) != 0)
            return ECOBIN_UART_RESULT_SAVED_STATUS_IDENTITY_CONFLICT;
        if (entry->state == ENTRY_RELEASED) return ECOBIN_UART_RESULT_SAVED_STATUS_ALREADY_RELEASED;
        entry->state = ENTRY_RELEASED;
        return ECOBIN_UART_RESULT_SAVED_STATUS_RELEASED;
    }
    return ECOBIN_UART_RESULT_SAVED_STATUS_NOT_FOUND;
}
