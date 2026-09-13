#include "native_serial_buffer.h"
#include <assert.h>
#include <string.h>

int main(void) {
    NativeRxBuffer ring;
    NativeScaleTransport scale;
    uint8_t bytes[512], frame[9] = {1, 3, 4, 0, 1, 0, 0, 0, 0};
    static const uint8_t first[] = {10u, 11u}, second[] = {12u, 13u, 14u};
    NativeSerialSpan spans[2];
    uint32_t i, measurement, attempt;
    uint64_t captured;
    NativeRx_Init(&ring);
    for (i = 0; i < 256; ++i) NativeRx_PushIrq(&ring, (uint8_t)i);
    assert(NativeRx_Read(&ring, bytes, 256) == 256);
    for (i = 0; i < 256; ++i) assert(bytes[i] == (uint8_t)i);
    for (i = 0; i < 400; ++i) NativeRx_PushIrq(&ring, (uint8_t)i);
    assert(NativeRx_Read(&ring, bytes, 200) == 200);
    for (i = 0; i < 200; ++i) NativeRx_PushIrq(&ring, (uint8_t)(i + 400));
    assert(NativeRx_Read(&ring, bytes, 512) == 400);
    for (i = 0; i < 400; ++i) assert(bytes[i] == (uint8_t)(i + 200));
    for (i = 0; i < 512; ++i) NativeRx_PushIrq(&ring, 1);
    assert(ring.overflow && NativeRx_Read(&ring, bytes, 512) == 0);
    assert(NativeRx_DiscardOverflow(&ring));
    assert(!NativeRx_DiscardOverflow(&ring));
    NativeRx_PushIrq(&ring, 42);
    assert(NativeRx_Read(&ring, bytes, 512) == 1 && bytes[0] == 42);
    spans[0].bytes = first; spans[0].length = sizeof(first);
    spans[1].bytes = second; spans[1].length = sizeof(second);
    assert(NativeRx_WriteAtomic(&ring, spans, 2u));
    assert(NativeRx_Read(&ring, bytes, sizeof(bytes)) == 5u);
    assert(memcmp(bytes, first, sizeof(first)) == 0
        && memcmp(bytes + sizeof(first), second, sizeof(second)) == 0);
    for (i = 0u; i < 510u; ++i) NativeRx_PushIrq(&ring, (uint8_t)i);
    i = ring.head;
    assert(!NativeRx_WriteAtomic(&ring, spans, 2u));
    assert(ring.head == i); /* insufficient room publishes zero span bytes */
    assert(NativeRx_Read(&ring, bytes, sizeof(bytes)) == 510u);
    for (i = 0u; i < 400u; ++i) NativeRx_PushIrq(&ring, (uint8_t)i);
    assert(NativeRx_Read(&ring, bytes, 200u) == 200u);
    assert(NativeRx_WriteAtomic(&ring, spans, 2u));
    assert(NativeRx_Read(&ring, bytes, sizeof(bytes)) == 205u);
    for (i = 0u; i < 200u; ++i) assert(bytes[i] == (uint8_t)(i + 200u));
    assert(memcmp(bytes + 200u, first, sizeof(first)) == 0
        && memcmp(bytes + 200u + sizeof(first), second, sizeof(second)) == 0);

    /* A QR command already accepted into the TX ring is immutable. A later
     * display command that does not fit publishes zero new bytes and cannot
     * discard, truncate or overwrite that earlier command. */
    {
        static const uint8_t qr[] = {'q', 'r', 0xffu, 0xffu, 0xffu};
        static const uint8_t later[] = {'p', 'a', 'g', 'e', 0xffu, 0xffu, 0xffu};
        NativeSerialSpan qr_span, later_span;
        NativeRx_Init(&ring);
        qr_span.bytes = qr; qr_span.length = sizeof(qr);
        later_span.bytes = later; later_span.length = sizeof(later);
        assert(NativeRx_WriteAtomic(&ring, &qr_span, 1u));
        for (i = 0u; i < 505u; ++i) NativeRx_PushIrq(&ring, (uint8_t)i);
        assert(!NativeRx_WriteAtomic(&ring, &later_span, 1u));
        assert(NativeRx_Read(&ring, bytes, sizeof(bytes)) == 510u);
        assert(memcmp(bytes, qr, sizeof(qr)) == 0);
    }

    NativeScale_Init(&scale);
    assert(NativeScale_Begin(&scale, 1, 1, 100, 200));
    assert(!NativeScale_Begin(&scale, 1, 2, 110, 200));
    for (i = 0; i < 9; ++i) NativeScale_ReceiveIrq(&scale, frame[i], 120);
    /* Foreground delay is not the capture time or a false timeout. */
    assert(!NativeScale_Expire(&scale, 900));
    assert(NativeScale_Take(&scale, bytes, &measurement, &attempt, &captured));
    assert(measurement == 1 && attempt == 1 && captured == 120 && memcmp(bytes, frame, 9) == 0);
    assert(!NativeScale_Take(&scale, bytes, &measurement, &attempt, &captured));
    assert(NativeScale_Begin(&scale, 1, 2, 900, 200));
    NativeScale_ReceiveIrq(&scale, 0xaa, 950);
    assert(!NativeScale_Expire(&scale, 1099));
    assert(NativeScale_Expire(&scale, 1100));
    assert(!NativeScale_CanBegin(&scale, 1149));
    NativeScale_ReceiveIrq(&scale, 0xbb, 1150); /* Idle late byte is discarded. */
    assert(!NativeScale_CanBegin(&scale, 1154));
    assert(NativeScale_Begin(&scale, 2, 3, 1155, 200));
    for (i = 0; i < 9; ++i) NativeScale_ReceiveIrq(&scale, frame[i], 1170);
    assert(NativeScale_Take(&scale, bytes, &measurement, &attempt, &captured));
    assert(measurement == 2 && attempt == 3 && memcmp(bytes, frame, 9) == 0);
    assert(NativeScale_Begin(&scale, 0, 4, 1500, 200)); /* Explicit idle read, no business measurement. */
    NativeScale_Cancel(&scale, 1510);
    for (i = 0; i < 9; ++i) NativeScale_ReceiveIrq(&scale, frame[i], 1520);
    assert(!NativeScale_Take(&scale, bytes, &measurement, &attempt, &captured));
    return 0;
}
