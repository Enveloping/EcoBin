#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "mcu_work_state.h"

static void from_hex(const char *hex, uint8_t *out, size_t length) {
    size_t i;
    assert(strlen(hex) == length * 2u);
    for (i = 0; i < length; ++i) {
        char byte[3];
        byte[0] = hex[i * 2u]; byte[1] = hex[i * 2u + 1u]; byte[2] = 0;
        out[i] = (uint8_t)strtoul(byte, NULL, 16);
    }
}

static void query(const McuWorkState *state, const uint8_t *request, uint8_t status, int emit) {
    McuWorkState before = *state;
    uint8_t reply[132];
    size_t i;
    assert(McuWorkState_Query(state, request, 86u, reply, sizeof(reply)) == sizeof(reply));
    assert(memcmp(&before, state, sizeof(before)) == 0); /* reads never release/change work */
    assert(memcmp(reply, request, 86u) == 0);
    assert(reply[ECOBIN_UART_WORK_QUERY_REPLY_STATUS_OFFSET] == status);
    assert(ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_WORK_QUERY_REPLY,
        reply, sizeof(reply)) == 0);
    if (emit) { for (i = 0; i < sizeof(reply); ++i) printf("%02x", reply[i]); puts(""); }
}

int main(int argc, char **argv) {
    McuWorkState state, before;
    uint8_t request[86], result[199], changed[86], copy[199], bad[199];
    uint32_t sequence;
    assert(argc == 3);
    from_hex(argv[1], request, sizeof(request)); from_hex(argv[2], result, sizeof(result));
    sequence = ecobin_uart_read_u32_be(request + ECOBIN_UART_QUERY_WORK_COMMAND_SEQUENCE_OFFSET);
    McuWorkState_Init(&state, 0u);
    assert(!McuWorkState_BeginAccepted(&state, request + 8u, 78u, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_PREPARING));
    query(&state, request, ECOBIN_UART_WORK_QUERY_STATUS_BOOT_MISMATCH, 0);
    McuWorkState_Init(&state, 42u);
    query(&state, request, ECOBIN_UART_WORK_QUERY_STATUS_NOT_FOUND, 0);
    assert(!McuWorkState_Complete(&state, result, sizeof(result)));
    before = state;
    assert(McuWorkState_CanBegin(&state, request + 8u, 78u, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_PREPARING));
    assert(!McuWorkState_CanBegin(NULL, request + 8u, 78u, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_PREPARING));
    assert(memcmp(&state, &before, sizeof(state)) == 0);
    assert(!McuWorkState_BeginAccepted(&state, request + 8u, 78u, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_ACTIVE));
    assert(McuWorkState_BeginAccepted(&state, request + 8u, 78u, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_PREPARING));
    assert(!McuWorkState_BeginAccepted(&state, request + 8u, 78u, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_PREPARING));
    assert(McuWorkState_SetPhase(&state, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COUNTDOWN));
    assert(!McuWorkState_SetPhase(&state, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_ACTIVE));
    query(&state, request, ECOBIN_UART_WORK_QUERY_STATUS_RUNNING, 1);
    memcpy(changed, request, sizeof(changed)); changed[24] ^= 1u;
    query(&state, changed, ECOBIN_UART_WORK_QUERY_STATUS_IDENTITY_CONFLICT, 0);
    memcpy(changed, request, sizeof(changed)); changed[8] ^= 1u; changed[68] ^= 1u;
    ecobin_uart_write_u32_be(changed + 64u, sequence + 1u);
    query(&state, changed, ECOBIN_UART_WORK_QUERY_STATUS_NOT_FOUND, 0);
    before = state;
    assert(!McuWorkState_Query(&state, request, 85u, copy, sizeof(copy)));
    assert(!McuWorkState_Query(&state, request, 86u, copy, 131u));
    memcpy(bad, result, sizeof(bad)); bad[80] ^= 1u;
    assert(!McuWorkState_Complete(&state, bad, sizeof(bad)));
    assert(memcmp(&state, &before, sizeof(state)) == 0);
    assert(McuWorkState_Complete(&state, result, sizeof(result)));
    assert(McuWorkState_Complete(&state, result, sizeof(result)));
    assert(!McuWorkState_SetPhase(&state, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_PREPARING));
    assert(!McuWorkState_BeginAccepted(&state, changed + 8u, 78u, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_PREPARING));
    query(&state, request, ECOBIN_UART_WORK_QUERY_STATUS_RESULT_HELD, 1);
    assert(McuWorkState_CopyHeld(&state, copy, sizeof(copy)) == sizeof(result));
    assert(memcmp(copy, result, sizeof(copy)) == 0);
    memcpy(bad, result, sizeof(bad)); bad[59] ^= 1u;
    assert(McuWorkState_Saved(&state, bad, 60u) == ECOBIN_UART_RESULT_SAVED_STATUS_IDENTITY_CONFLICT);
    assert(McuWorkState_Saved(&state, result, 60u) == ECOBIN_UART_RESULT_SAVED_STATUS_RELEASED);
    assert(McuWorkState_Saved(&state, result, 60u) == ECOBIN_UART_RESULT_SAVED_STATUS_ALREADY_RELEASED);
    query(&state, request, ECOBIN_UART_WORK_QUERY_STATUS_RESULT_RELEASED, 1);
    assert(!McuWorkState_CopyHeld(&state, copy, sizeof(copy)));
    assert(!McuWorkState_Complete(&state, result, sizeof(result)));
    assert(!McuWorkState_BeginAccepted(&state, request + 8u, 78u, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_PREPARING));
    assert(McuWorkState_BeginAccepted(&state, changed + 8u, 78u, ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_PREPARING));
    query(&state, request, ECOBIN_UART_WORK_QUERY_STATUS_NOT_FOUND, 0);
    assert(!McuWorkState_Complete(&state, result, sizeof(result))); /* result of another work */
    assert(McuWorkState_Saved(&state, result, 60u) == ECOBIN_UART_RESULT_SAVED_STATUS_ALREADY_RELEASED);
    query(&state, changed, ECOBIN_UART_WORK_QUERY_STATUS_RUNNING, 0); /* old receipt cannot end new work */
    McuWorkState_Init(&state, 43u);
    query(&state, request, ECOBIN_UART_WORK_QUERY_STATUS_BOOT_MISMATCH, 0);
    McuWorkState_Init(&state, 42u);
    memcpy(changed, request, sizeof(changed)); changed[84] = ECOBIN_UART_WORK_TYPE_CLEAN_OPERATION;
    assert(McuWorkState_BeginAccepted(&state, changed + 8u, 78u, ECOBIN_UART_MCU_WORK_PHASE_CLEAN_ACTIVE));
    query(&state, changed, ECOBIN_UART_WORK_QUERY_STATUS_RUNNING, 0);
    return 0;
}
