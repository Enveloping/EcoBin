#include <assert.h>
#include "scale_reader.h"

static void seal(uint8_t *frame)
{
    uint16_t crc = ScaleReader_Crc16(frame, 7U);
    frame[7] = (uint8_t)crc;
    frame[8] = (uint8_t)(crc >> 8);
}
int main(void)
{
    uint8_t frame[] = {1U, 3U, 4U, 0xFFU, 0xF7U, 0xFFU, 0xFFU, 0U, 0U};
    int32_t grams = 0;
    assert(ScaleReader_Crc16((const uint8_t *)"123456789", 9U) == 0x4B37U);
    seal(frame);
    assert(ScaleReader_Decode(frame, 9U, -350000, 350000, &grams) == SCALE_READER_OK);
    assert(grams == -9);
    frame[0] = 2U;
    seal(frame); /* Correct CRC does not make another slave's response legal. */
    assert(ScaleReader_Decode(frame, 9U, -350000, 350000, &grams) == SCALE_READER_PROTOCOL_ERROR);
    assert(grams == -9); /* Error cannot be confused with a measured zero. */
    frame[0] = 1U;
    frame[1] = 0x83U;
    seal(frame);
    assert(ScaleReader_Decode(frame, 9U, -350000, 350000, &grams) == SCALE_READER_PROTOCOL_ERROR);
    frame[1] = 3U;
    frame[2] = 2U;
    seal(frame);
    assert(ScaleReader_Decode(frame, 9U, -350000, 350000, &grams) == SCALE_READER_PROTOCOL_ERROR);
    frame[2] = 4U;
    frame[3] = 1U; frame[4] = 0xF4U; frame[5] = 0U; frame[6] = 0U;
    seal(frame);
    assert(ScaleReader_Decode(frame, 9U, -350000, 350000, &grams) == SCALE_READER_OK);
    assert(grams == 500);
    assert(ScaleReader_Decode(frame, 9U, -100, 499, &grams) == SCALE_READER_RANGE_ERROR);
    frame[8] ^= 1U;
    assert(ScaleReader_Decode(frame, 9U, -350000, 350000, &grams) == SCALE_READER_CRC_ERROR);
    assert(ScaleReader_Decode(frame, 8U, -350000, 350000, &grams) == SCALE_READER_PROTOCOL_ERROR);
    assert(ScaleReader_Decode(frame, 10U, -350000, 350000, &grams) == SCALE_READER_PROTOCOL_ERROR);
    frame[3] = 0U; frame[4] = 0U; frame[5] = 0x80U; frame[6] = 0U;
    seal(frame);
    assert(ScaleReader_Decode(frame, 9U, INT32_MIN, INT32_MAX, &grams) == SCALE_READER_OK);
    assert(grams == INT32_MIN);
    return 0;
}
