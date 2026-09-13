#include "mcu_session.h"
/* Explicit candidate include prevents accidental use of frozen UART 1 enums. */
#include "../../contracts/uart/generated/c/ecobin_uart_protocol.h"

static uint8_t valid_identity(uint64_t value)
{
    return value > 0U && value <= (((uint64_t)1U << 53U) - 1U);
}

void McuSession_Init(McuSession *session)
{
    memset(session, 0, sizeof(*session));
}

uint8_t McuSession_Probe(McuSession *session, uint64_t probe_id,
                         uint64_t *current_boot_id)
{
    if(!valid_identity(probe_id)) return 0U;
    if(session->boot_id == 0U) session->pending_probe_id = probe_id;
    *current_boot_id = session->boot_id;
    return 1U;
}

uint8_t McuSession_Bind(McuSession *session, uint64_t probe_id,
                        uint64_t proposed_boot_id, McuSessionBindingReply *reply)
{
    if(!valid_identity(probe_id) || !valid_identity(proposed_boot_id)) return 0U;
    if(session->boot_id != 0U)
        reply->status = ECOBIN_UART_BOOT_BIND_STATUS_ALREADY_BOUND;
    else if(session->pending_probe_id != probe_id)
        reply->status = ECOBIN_UART_BOOT_BIND_STATUS_PROBE_MISMATCH;
    else
    {
        session->boot_id = proposed_boot_id;
        session->pending_probe_id = 0U;
        reply->status = ECOBIN_UART_BOOT_BIND_STATUS_BOUND;
    }
    reply->current_boot_id = session->boot_id;
    return 1U;
}

static uint8_t valid_command(const McuSessionCommand *command)
{
    return valid_identity(command->target_boot_id) && command->sequence != 0U
        && !ecobin_uart_bytes_zero(command->uid, sizeof(command->uid));
}

static uint8_t same_command(const McuSessionCommand *a, const McuSessionCommand *b)
{
    /* Never compare struct padding. */
    return a->target_boot_id == b->target_boot_id && a->sequence == b->sequence
        && memcmp(a->uid, b->uid, sizeof(a->uid)) == 0
        && memcmp(a->digest, b->digest, sizeof(a->digest)) == 0;
}

static void latest_decision(const McuSession *session, McuSessionDecision *decision)
{
    decision->error_code = session->latest_error;
    decision->outcome = session->latest_error == ECOBIN_UART_NACK_ERROR_NONE
        ? ECOBIN_UART_COMMAND_OUTCOME_ACCEPTED : ECOBIN_UART_COMMAND_OUTCOME_REJECTED;
}

uint8_t McuSession_ReceiveCommand(McuSession *session,
    const McuSessionCommand *command, uint16_t business_error,
    McuSessionDecision *decision)
{
    decision->execute_once = 0U;
    if(business_error > ECOBIN_UART_NACK_ERROR_INTERNAL_FAULT
        || !McuSession_QueryCommand(session, command, decision))
        return 0U;
    if(decision->outcome == ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN)
    {
        session->highest_sequence = command->sequence;
        session->latest_command = *command;
        session->latest_error = business_error;
        decision->highest_sequence = session->highest_sequence;
        latest_decision(session, decision);
        decision->execute_once = business_error == ECOBIN_UART_NACK_ERROR_NONE;
    }
    return 1U;
}

uint8_t McuSession_QueryCommand(const McuSession *session,
    const McuSessionCommand *command, McuSessionDecision *decision)
{
    decision->execute_once = 0U;
    if(!valid_command(command)) return 0U;
    memset(decision, 0, sizeof(*decision));
    decision->command = *command;
    decision->current_boot_id = session->boot_id;
    decision->highest_sequence = session->highest_sequence;
    if(session->boot_id != command->target_boot_id)
        decision->outcome = ECOBIN_UART_COMMAND_OUTCOME_BOOT_MISMATCH;
    else if(command->sequence == session->highest_sequence)
    {
        if(same_command(command, &session->latest_command))
            latest_decision(session, decision);
        else
            decision->outcome = ECOBIN_UART_COMMAND_OUTCOME_IDENTITY_CONFLICT;
    }
    else if(command->sequence < session->highest_sequence)
        decision->outcome = ECOBIN_UART_COMMAND_OUTCOME_OLD_DETAILS_UNAVAILABLE;
    else
        decision->outcome = ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN;
    return 1U;
}
