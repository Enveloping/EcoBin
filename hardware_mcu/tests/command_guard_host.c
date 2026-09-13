#include "../../contracts/uart/generated/c/ecobin_uart_protocol.h"
int TestCommandGuard(const uint8_t *frame, size_t length) {
    ecobin_uart_frame_view_t view;
    return ecobin_uart_validate_frame(frame, length, ECOBIN_UART_SENDER_ROLE_EDGE, &view);
}
int TestMcuFrameGuard(const uint8_t *frame, size_t length) {
    ecobin_uart_frame_view_t view;
    return ecobin_uart_validate_frame(frame, length, ECOBIN_UART_SENDER_ROLE_MCU, &view);
}
