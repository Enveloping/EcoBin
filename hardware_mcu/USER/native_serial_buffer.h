#ifndef ECOBIN_NATIVE_SERIAL_BUFFER_H
#define ECOBIN_NATIVE_SERIAL_BUFFER_H
#include <stddef.h>
#include <stdint.h>

#define NATIVE_RX_CAPACITY 512u
/* Single ISR producer / single foreground consumer. Indices are aligned native
 * halfwords. Reset/take-overflow must be called with the producer masked. */
typedef struct {
    uint8_t bytes[NATIVE_RX_CAPACITY];
    volatile uint16_t head, tail;
    volatile uint8_t overflow;
} NativeRxBuffer;
typedef struct {
    const uint8_t *bytes;
    size_t length;
} NativeSerialSpan;
void NativeRx_Init(NativeRxBuffer *buffer);
void NativeRx_PushIrq(NativeRxBuffer *buffer, uint8_t byte);
size_t NativeRx_Read(NativeRxBuffer *buffer, uint8_t *output, size_t capacity);
uint8_t NativeRx_DiscardOverflow(NativeRxBuffer *buffer);
/* Caller excludes the consumer while this runs. All span bytes become visible
 * with one final head update, or zero bytes become visible. Never sets or
 * clears overflow. Capacity keeps one ring slot empty. */
uint8_t NativeRx_WriteAtomic(NativeRxBuffer *buffer,
    const NativeSerialSpan *spans, size_t count);

typedef struct {
    uint8_t bytes[9];
    volatile uint8_t length, active, complete;
    uint32_t measurement, attempt;
    uint64_t started_ms, deadline_ms;
    volatile uint64_t captured_ms, last_rx_ms;
    uint64_t reuse_after_ms;
} NativeScaleTransport;
/* All foreground calls must mask the RX producer. One outstanding query only.
 * Timeout closes and clears that request; an idle quiet period drops late bytes.
 * Quiet time is a practical framing guard, NOT proof of a vendor response bound.
 * Arbitrarily delayed unnumbered Modbus responses still require real-board
 * qualification. Never claim these local IDs were echoed by the transmitter. */
void NativeScale_Init(NativeScaleTransport *transport);
uint8_t NativeScale_Begin(NativeScaleTransport *transport, uint32_t measurement,
    uint32_t attempt, uint64_t now_ms, uint32_t timeout_ms);
void NativeScale_ReceiveIrq(NativeScaleTransport *transport, uint8_t byte, uint64_t now_ms);
uint8_t NativeScale_Take(NativeScaleTransport *transport, uint8_t *output,
    uint32_t *measurement, uint32_t *attempt, uint64_t *captured_ms);
uint8_t NativeScale_Expire(NativeScaleTransport *transport, uint64_t now_ms);
void NativeScale_Cancel(NativeScaleTransport *transport, uint64_t now_ms);
uint8_t NativeScale_CanBegin(const NativeScaleTransport *transport, uint64_t now_ms);
#endif
