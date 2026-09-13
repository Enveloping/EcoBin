#include "scale_reader.h"

uint16_t ScaleReader_Crc16(const uint8_t *bytes, uint8_t length)
{
    uint16_t crc = 0xFFFFU;
    uint8_t bit;
    while(length--)
    {
        crc ^= *bytes++;
        for(bit = 0U; bit < 8U; bit++)
            crc = (uint16_t)((crc >> 1) ^ ((crc & 1U) ? 0xA001U : 0U));
    }
    return crc;
}

uint8_t ScaleReader_Decode(const uint8_t *frame, uint8_t length,
    int32_t minimum_grams, int32_t maximum_grams, int32_t *grams)
{
    uint16_t crc;
    uint32_t raw;
    int32_t value;
    if(frame == 0 || grams == 0 || length != 9U || frame[0] != 1U ||
       frame[1] != 3U || frame[2] != 4U)
        return SCALE_READER_PROTOCOL_ERROR;
    crc = ScaleReader_Crc16(frame, 7U);
    if(frame[7] != (uint8_t)crc || frame[8] != (uint8_t)(crc >> 8))
        return SCALE_READER_CRC_ERROR;
    raw = ((uint32_t)frame[5] << 24) | ((uint32_t)frame[6] << 16) |
          ((uint32_t)frame[3] << 8) | frame[4];
    value = (raw & 0x80000000UL) ? -1 - (int32_t)(UINT32_MAX - raw) : (int32_t)raw;
    if(minimum_grams > maximum_grams || value < minimum_grams || value > maximum_grams)
        return SCALE_READER_RANGE_ERROR;
    *grams = value;
    return SCALE_READER_OK;
}
