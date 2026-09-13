#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "mcu_result_slot.h"
#include "../../contracts/uart/generated/c/ecobin_uart_protocol.h"

typedef char result_ram_budget[(sizeof(McuResultSlot) <= 224u) ? 1 : -1];

static void from_hex(const char *hex, uint8_t *out) {
    size_t index;
    assert(strlen(hex) == 398u);
    for (index = 0u; index < 199u; ++index) {
        char byte[3];
        byte[0] = hex[index * 2u]; byte[1] = hex[index * 2u + 1u]; byte[2] = 0;
        out[index] = (uint8_t)strtoul(byte, NULL, 16);
    }
}

int main(int argc, char **argv) {
    McuResultSlot slot;
    uint8_t first[199], next[199], copy[199], identity[60], bad[199];
    assert(argc == 3);
    from_hex(argv[1], first); from_hex(argv[2], next);
    McuResultSlot_Init(&slot, 42u);
    assert(McuResultSlot_CopyHeld(&slot, copy, sizeof(copy)) == 0u);
    assert(McuResultSlot_Freeze(&slot, first, sizeof(first)) == 1u);
    assert(McuResultSlot_Freeze(&slot, first, sizeof(first)) == 1u);
    assert(McuResultSlot_Query(&slot, first, 60u) == ECOBIN_UART_RESULT_QUERY_STATUS_HELD);
    assert(McuResultSlot_CopyHeld(&slot, copy, sizeof(copy)) == 199u);
    assert(memcmp(copy, first, sizeof(copy)) == 0);
    memcpy(bad, first, sizeof(bad)); bad[150] ^= 1u;
    assert(McuResultSlot_Freeze(&slot, bad, sizeof(bad)) == 0u);
    assert(McuResultSlot_Freeze(&slot, first, 198u) == 0u);
    assert(McuResultSlot_Freeze(&slot, next, sizeof(next)) == 0u);
    assert(McuResultSlot_CopyHeld(&slot, copy, 198u) == 0u);
    memcpy(identity, first, sizeof(identity)); identity[59] ^= 1u;
    assert(McuResultSlot_Saved(&slot, identity, 60u) == ECOBIN_UART_RESULT_SAVED_STATUS_IDENTITY_CONFLICT);
    assert(McuResultSlot_Saved(&slot, first, 59u) == 0u);
    assert(McuResultSlot_CopyHeld(&slot, copy, sizeof(copy)) == 199u);
    assert(McuResultSlot_Saved(&slot, first, 60u) == ECOBIN_UART_RESULT_SAVED_STATUS_RELEASED);
    assert(McuResultSlot_Saved(&slot, first, 60u) == ECOBIN_UART_RESULT_SAVED_STATUS_ALREADY_RELEASED);
    assert(McuResultSlot_Query(&slot, first, 60u) == ECOBIN_UART_RESULT_QUERY_STATUS_RELEASED);
    assert(McuResultSlot_CopyHeld(&slot, copy, sizeof(copy)) == 0u);
    assert(McuResultSlot_Freeze(&slot, first, sizeof(first)) == 0u);
    assert(McuResultSlot_Freeze(&slot, next, sizeof(next)) == 1u);
    assert(McuResultSlot_Saved(&slot, first, 60u) == ECOBIN_UART_RESULT_SAVED_STATUS_NOT_FOUND);
    assert(McuResultSlot_CopyHeld(&slot, copy, sizeof(copy)) == 199u);
    assert(memcmp(copy, next, sizeof(copy)) == 0);
    assert(McuResultSlot_Saved(&slot, next, 60u) == ECOBIN_UART_RESULT_SAVED_STATUS_RELEASED);
    McuResultSlot_Init(&slot, 43u);
    assert(McuResultSlot_CopyHeld(&slot, copy, sizeof(copy)) == 0u);
    assert(McuResultSlot_Saved(&slot, next, 60u) == ECOBIN_UART_RESULT_SAVED_STATUS_BOOT_MISMATCH);
    assert(McuResultSlot_Freeze(&slot, first, sizeof(first)) == 0u);
    return 0;
}
