#include <assert.h>
#include <stdio.h>
#include "mcu_session.h"
/* Explicit candidate path: never silently include the frozen USER/uar header. */
#include "../../contracts/uart/generated/c/ecobin_uart_protocol.h"

/* Compile these resource ceilings with both the host and target compilers.
 * They cover this core only, not the wire parser, work/result slot or stack. */
typedef char SessionRamBudget[(sizeof(McuSession) <= 96U) ? 1 : -1];
typedef char DecisionRamBudget[(sizeof(McuSessionDecision) <= 80U) ? 1 : -1];

static void test_binding_is_ram_only_and_cannot_overwrite_a_bound_boot(void)
{
    McuSession session;
    McuSessionBindingReply reply;
    uint64_t observed = 999U;
    McuSession_Init(&session);
    assert(McuSession_Probe(&session, 10U, &observed));
    assert(observed == 0U);
    assert(McuSession_Bind(&session, 9U, 40U, &reply));
    assert(reply.status == ECOBIN_UART_BOOT_BIND_STATUS_PROBE_MISMATCH);
    assert(reply.current_boot_id == 0U);
    assert(McuSession_Bind(&session, 10U, 41U, &reply));
    assert(reply.status == ECOBIN_UART_BOOT_BIND_STATUS_BOUND);
    assert(reply.current_boot_id == 41U);
    assert(McuSession_Probe(&session, 11U, &observed));
    assert(observed == 41U);
    assert(McuSession_Bind(&session, 11U, 42U, &reply));
    assert(reply.status == ECOBIN_UART_BOOT_BIND_STATUS_ALREADY_BOUND);
    assert(reply.current_boot_id == 41U);
    McuSession_Init(&session);
    assert(McuSession_Bind(&session, 10U, 41U, &reply));
    assert(reply.status == ECOBIN_UART_BOOT_BIND_STATUS_PROBE_MISMATCH);
    assert(reply.current_boot_id == 0U);
    assert(McuSession_Probe(&session, 12U, &observed));
    assert(McuSession_Bind(&session, 12U, 42U, &reply));
    assert(reply.status == ECOBIN_UART_BOOT_BIND_STATUS_BOUND);
    assert(reply.current_boot_id == 42U);
}

static McuSessionCommand command(uint64_t boot_id, uint32_t sequence)
{
    McuSessionCommand value;
    memset(&value, 0, sizeof(value));
    value.target_boot_id = boot_id;
    value.sequence = sequence;
    memset(value.uid, 0x11, sizeof(value.uid));
    memset(value.digest, 0xAB, sizeof(value.digest));
    return value;
}

static void bind_boot(McuSession *session, uint64_t boot_id)
{
    uint64_t observed;
    McuSessionBindingReply reply;
    assert(McuSession_Probe(session, boot_id, &observed));
    assert(McuSession_Bind(session, boot_id, boot_id, &reply));
    assert(reply.status == ECOBIN_UART_BOOT_BIND_STATUS_BOUND);
}

static void test_lost_reply_does_not_execute_command_twice(void)
{
    McuSession session;
    McuSessionDecision decision;
    McuSessionCommand request = command(42U, 7U);
    unsigned actions = 0U;
    McuSession_Init(&session);
    bind_boot(&session, 42U);
    assert(McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    actions += decision.execute_once;
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_ACCEPTED);
    assert(decision.current_boot_id == 42U);
    assert(decision.highest_sequence == 7U);
    assert(decision.command.target_boot_id == request.target_boot_id);
    assert(decision.command.sequence == request.sequence);
    assert(memcmp(decision.command.uid, request.uid, sizeof(request.uid)) == 0);
    assert(memcmp(decision.command.digest, request.digest, sizeof(request.digest)) == 0);
    /* A later business-state check cannot change the original decision. */
    assert(McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_BUSY, &decision));
    actions += decision.execute_once;
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_ACCEPTED);
    assert(decision.error_code == ECOBIN_UART_NACK_ERROR_NONE);
    assert(actions == 1U);
}

static void test_query_is_read_only_and_distinguishes_retired_from_not_seen(void)
{
    McuSession session;
    McuSessionDecision decision;
    McuSessionCommand request = command(42U, 7U);
    McuSession_Init(&session);
    bind_boot(&session, 42U);
    assert(McuSession_QueryCommand(&session, &request, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN);
    assert(decision.highest_sequence == 0U);
    assert(!decision.execute_once);
    assert(McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_BUSY, &decision));
    assert(!decision.execute_once);
    assert(McuSession_QueryCommand(&session, &request, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_REJECTED);
    assert(decision.error_code == ECOBIN_UART_NACK_ERROR_BUSY);
    assert(decision.highest_sequence == 7U);
    request.sequence = 100U;
    assert(McuSession_QueryCommand(&session, &request, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN);
    assert(decision.highest_sequence == 7U);
    request.sequence = 8U;
    assert(McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.execute_once);
    request.sequence = 7U;
    assert(McuSession_QueryCommand(&session, &request, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_OLD_DETAILS_UNAVAILABLE);
    assert(decision.error_code == ECOBIN_UART_NACK_ERROR_NONE);
    assert(decision.highest_sequence == 8U);
    assert(!decision.execute_once);
}

static void test_rejection_and_identity_conflicts_never_repeat_an_action(void)
{
    McuSession session;
    McuSessionDecision decision;
    McuSessionCommand original = command(42U, 7U);
    McuSessionCommand changed = original;
    McuSession_Init(&session);
    bind_boot(&session, 42U);
    assert(McuSession_ReceiveCommand(&session, &original, ECOBIN_UART_NACK_ERROR_BUSY, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_REJECTED);
    assert(!decision.execute_once);
    assert(McuSession_ReceiveCommand(&session, &original, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_REJECTED);
    assert(decision.error_code == ECOBIN_UART_NACK_ERROR_BUSY);
    assert(!decision.execute_once);
    changed.uid[0] ^= 1U;
    assert(McuSession_ReceiveCommand(&session, &changed, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_IDENTITY_CONFLICT);
    assert(!decision.execute_once);
    changed = original;
    changed.digest[31] ^= 1U;
    assert(McuSession_QueryCommand(&session, &changed, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_IDENTITY_CONFLICT);
    assert(!decision.execute_once);
    assert(McuSession_ReceiveCommand(&session, &changed, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_IDENTITY_CONFLICT);
    assert(!decision.execute_once);
    assert(McuSession_QueryCommand(&session, &original, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_REJECTED);
    assert(decision.error_code == ECOBIN_UART_NACK_ERROR_BUSY);
}

static void test_skipped_sequences_are_retired_and_exhaustion_cannot_wrap(void)
{
    McuSession session;
    McuSessionDecision decision;
    McuSessionCommand request = command(42U, 10U);
    uint32_t sequence;
    McuSession_Init(&session);
    bind_boot(&session, 42U);
    assert(McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.execute_once);
    for(sequence = 1U; sequence < 10U; ++sequence)
    {
        request.sequence = sequence;
        assert(McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_NONE, &decision));
        assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_OLD_DETAILS_UNAVAILABLE);
        assert(!decision.execute_once);
        assert(decision.highest_sequence == 10U);
    }
    request.sequence = UINT32_MAX;
    assert(McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.execute_once);
    request.sequence = 0U;
    assert(!McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    request.sequence = 1U;
    assert(McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_OLD_DETAILS_UNAVAILABLE);
    assert(!decision.execute_once);
    request.sequence = UINT32_MAX;
    assert(McuSession_QueryCommand(&session, &request, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_ACCEPTED);
    assert(decision.highest_sequence == UINT32_MAX);
    assert(!decision.execute_once);
}

static void test_restart_blocks_old_commands_before_and_after_rebinding(void)
{
    McuSession session;
    McuSessionDecision decision;
    McuSessionCommand old = command(42U, 7U);
    McuSessionCommand next = command(43U, 1U);
    McuSession_Init(&session);
    bind_boot(&session, 42U);
    assert(McuSession_ReceiveCommand(&session, &old, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.execute_once);
    McuSession_Init(&session);
    assert(McuSession_ReceiveCommand(&session, &old, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_BOOT_MISMATCH);
    assert(decision.current_boot_id == 0U && decision.highest_sequence == 0U);
    assert(!decision.execute_once);
    bind_boot(&session, 43U);
    assert(McuSession_ReceiveCommand(&session, &old, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_BOOT_MISMATCH);
    assert(decision.current_boot_id == 43U && decision.highest_sequence == 0U);
    assert(!decision.execute_once);
    assert(McuSession_ReceiveCommand(&session, &next, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.execute_once);
}

static void test_invalid_identity_does_not_consume_probe_or_command(void)
{
    McuSession session;
    McuSessionBindingReply binding;
    McuSessionDecision decision;
    McuSessionCommand request = command(42U, 7U);
    uint64_t observed = 999U;
    uint64_t overflow = (uint64_t)1U << 53U;
    McuSession_Init(&session);
    assert(McuSession_Probe(&session, 10U, &observed));
    assert(!McuSession_Probe(&session, 0U, &observed));
    assert(!McuSession_Probe(&session, overflow, &observed));
    assert(!McuSession_Bind(&session, 10U, overflow, &binding));
    assert(!McuSession_Bind(&session, 10U, 0U, &binding));
    assert(McuSession_Bind(&session, 10U, 42U, &binding));
    assert(binding.status == ECOBIN_UART_BOOT_BIND_STATUS_BOUND);
    memset(request.uid, 0, sizeof(request.uid));
    assert(!McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    request = command(overflow, 7U);
    assert(!McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    request = command(0U, 7U);
    assert(!McuSession_QueryCommand(&session, &request, &decision));
    request = command(42U, 7U);
    assert(!McuSession_ReceiveCommand(&session, &request, UINT16_MAX, &decision));
    assert(McuSession_QueryCommand(&session, &request, &decision));
    assert(decision.outcome == ECOBIN_UART_COMMAND_OUTCOME_NOT_SEEN);
    assert(decision.highest_sequence == 0U);
    assert(McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.execute_once);
    /* Reusing a reply buffer must not leave an old execute flag on failure. */
    assert(!McuSession_ReceiveCommand(&session, &request, UINT16_MAX, &decision));
    assert(!decision.execute_once);
    request.sequence = 8U;
    assert(McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert(decision.execute_once);
    request.target_boot_id = 0U;
    assert(!McuSession_QueryCommand(&session, &request, &decision));
    assert(!decision.execute_once);
}

static void test_latest_probe_retires_the_previous_binding_candidate(void)
{
    McuSession session;
    McuSessionBindingReply reply;
    uint64_t observed;
    McuSession_Init(&session);
    assert(McuSession_Probe(&session, 10U, &observed));
    assert(McuSession_Probe(&session, 11U, &observed));
    assert(McuSession_Bind(&session, 10U, 41U, &reply));
    assert(reply.status == ECOBIN_UART_BOOT_BIND_STATUS_PROBE_MISMATCH);
    assert(McuSession_Bind(&session, 11U, 42U, &reply));
    assert(reply.status == ECOBIN_UART_BOOT_BIND_STATUS_BOUND);
}

static void assert_query_reply_is_contract_valid(const McuSessionDecision *decision)
{
    uint8_t payload[ECOBIN_UART_COMMAND_QUERY_RESULT_PAYLOAD_MAX_LENGTH];
    memset(payload, 0, sizeof(payload));
    ecobin_uart_write_u64_be(payload + ECOBIN_UART_COMMAND_QUERY_RESULT_QUERY_ID_OFFSET, 99U);
    memcpy(payload + ECOBIN_UART_COMMAND_QUERY_RESULT_MCU_COMMAND_UID_OFFSET,
           decision->command.uid, sizeof(decision->command.uid));
    memcpy(payload + ECOBIN_UART_COMMAND_QUERY_RESULT_COMMAND_DIGEST_SHA256_OFFSET,
           decision->command.digest, sizeof(decision->command.digest));
    ecobin_uart_write_u64_be(payload + ECOBIN_UART_COMMAND_QUERY_RESULT_TARGET_MCU_BOOT_ID_OFFSET,
                             decision->command.target_boot_id);
    ecobin_uart_write_u32_be(payload + ECOBIN_UART_COMMAND_QUERY_RESULT_COMMAND_SEQUENCE_OFFSET,
                             decision->command.sequence);
    ecobin_uart_write_u64_be(payload + ECOBIN_UART_COMMAND_QUERY_RESULT_CURRENT_MCU_BOOT_ID_OFFSET,
                             decision->current_boot_id);
    payload[ECOBIN_UART_COMMAND_QUERY_RESULT_OUTCOME_OFFSET] = decision->outcome;
    ecobin_uart_write_u16_be(payload + ECOBIN_UART_COMMAND_QUERY_RESULT_ERROR_CODE_OFFSET,
                             decision->error_code);
    ecobin_uart_write_u32_be(payload + ECOBIN_UART_COMMAND_QUERY_RESULT_HIGHEST_COMMAND_SEQUENCE_OFFSET,
                             decision->highest_sequence);
    assert(ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_COMMAND_QUERY_RESULT,
                                                payload, sizeof(payload)) == 0);
}

static void test_all_decision_branches_match_generated_reply_contract(void)
{
    McuSession session;
    McuSessionDecision decision;
    McuSessionCommand request = command(42U, 7U);
    McuSession_Init(&session);
    assert(McuSession_QueryCommand(&session, &request, &decision));
    assert_query_reply_is_contract_valid(&decision);
    bind_boot(&session, 42U);
    assert(McuSession_QueryCommand(&session, &request, &decision));
    assert_query_reply_is_contract_valid(&decision);
    assert(McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_BUSY, &decision));
    assert_query_reply_is_contract_valid(&decision);
    request.uid[0] ^= 1U;
    assert(McuSession_QueryCommand(&session, &request, &decision));
    assert_query_reply_is_contract_valid(&decision);
    request.sequence = 9U;
    assert(McuSession_ReceiveCommand(&session, &request, ECOBIN_UART_NACK_ERROR_NONE, &decision));
    assert_query_reply_is_contract_valid(&decision);
    request.sequence = 8U;
    assert(McuSession_QueryCommand(&session, &request, &decision));
    assert_query_reply_is_contract_valid(&decision);
    request.target_boot_id = 43U;
    assert(McuSession_QueryCommand(&session, &request, &decision));
    assert_query_reply_is_contract_valid(&decision);
}

int main(void)
{
    test_binding_is_ram_only_and_cannot_overwrite_a_bound_boot();
    test_lost_reply_does_not_execute_command_twice();
    test_query_is_read_only_and_distinguishes_retired_from_not_seen();
    test_rejection_and_identity_conflicts_never_repeat_an_action();
    test_skipped_sequences_are_retired_and_exhaustion_cannot_wrap();
    test_restart_blocks_old_commands_before_and_after_rebinding();
    test_invalid_identity_does_not_consume_probe_or_command();
    test_latest_probe_retires_the_previous_binding_candidate();
    test_all_decision_branches_match_generated_reply_contract();
    printf("MCU session core: 9 scenarios passed; RAM state=%u decision=%u bytes\n",
           (unsigned)sizeof(McuSession), (unsigned)sizeof(McuSessionDecision));
    return 0;
}
