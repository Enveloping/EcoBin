#include "native_serial_buffer.h"
#include <string.h>

void NativeRx_Init(NativeRxBuffer *buffer) { memset(buffer, 0, sizeof(*buffer)); }
void NativeRx_PushIrq(NativeRxBuffer *buffer, uint8_t byte) {
    uint16_t next = (uint16_t)((buffer->head + 1u) & (NATIVE_RX_CAPACITY - 1u));
    if (next == buffer->tail || buffer->overflow) { buffer->overflow = 1u; return; }
    buffer->bytes[buffer->head] = byte;
    buffer->head = next;
}
size_t NativeRx_Read(NativeRxBuffer *buffer, uint8_t *output, size_t capacity) {
    size_t length = 0u;
    while (length < capacity && buffer->tail != buffer->head && !buffer->overflow) {
        output[length++] = buffer->bytes[buffer->tail];
        buffer->tail = (uint16_t)((buffer->tail + 1u) & (NATIVE_RX_CAPACITY - 1u));
    }
    return length;
}
uint8_t NativeRx_DiscardOverflow(NativeRxBuffer *buffer) {
    if (!buffer->overflow) return 0u;
    buffer->tail = buffer->head;
    buffer->overflow = 0u;
    return 1u;
}

void NativeScale_Init(NativeScaleTransport *transport) { memset(transport, 0, sizeof(*transport)); }
uint8_t NativeScale_CanBegin(const NativeScaleTransport *transport, uint64_t now) {
    return (uint8_t)(!transport->active && !transport->complete && now >= transport->reuse_after_ms
        && (transport->last_rx_ms == 0u || now >= transport->last_rx_ms + 5u));
}
uint8_t NativeScale_Begin(NativeScaleTransport *transport, uint32_t measurement,
    uint32_t attempt, uint64_t now, uint32_t timeout) {
    if (!attempt || !timeout || !NativeScale_CanBegin(transport, now)) return 0u;
    transport->measurement = measurement;
    transport->attempt = attempt;
    transport->started_ms = now;
    transport->deadline_ms = now + timeout;
    transport->length = transport->complete = 0u;
    transport->active = 1u;
    return 1u;
}
void NativeScale_ReceiveIrq(NativeScaleTransport *transport, uint8_t byte, uint64_t now) {
    transport->last_rx_ms = now;
    if (!transport->active || transport->complete || now > transport->deadline_ms) return;
    transport->bytes[transport->length++] = byte;
    if (transport->length == 9u) { transport->captured_ms = now; transport->complete = 1u; }
}
uint8_t NativeScale_Take(NativeScaleTransport *transport, uint8_t *output,
    uint32_t *measurement, uint32_t *attempt, uint64_t *captured) {
    if (!transport->complete) return 0u;
    memcpy(output, transport->bytes, 9u);
    *measurement = transport->measurement;
    *attempt = transport->attempt;
    *captured = transport->captured_ms;
    transport->active = transport->complete = transport->length = 0u;
    return 1u;
}
void NativeScale_Cancel(NativeScaleTransport *transport, uint64_t now) {
    transport->active = transport->complete = transport->length = 0u;
    transport->reuse_after_ms = now + 50u;
}
uint8_t NativeScale_Expire(NativeScaleTransport *transport, uint64_t now) {
    if (!transport->active || transport->complete || now < transport->deadline_ms) return 0u;
    NativeScale_Cancel(transport, now);
    return 1u;
}
