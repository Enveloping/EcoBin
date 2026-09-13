#ifndef ECOBIN_SCALE_READER_H
#define ECOBIN_SCALE_READER_H
#include <stdint.h>
#define SCALE_READER_OK 0U
#define SCALE_READER_TIMEOUT 2U
#define SCALE_READER_CRC_ERROR 3U
#define SCALE_READER_RANGE_ERROR 4U
#define SCALE_READER_PROTOCOL_ERROR 5U

/* One completed, locally owned read. attempt_sequence == 0 means absent.
 * status uses SCALE_READER_* (not UART enum numbers). Capture time and the
 * calibration/port used for that attempt survive later phase/config changes. */
typedef struct {
    uint64_t captured_ms;
    uint32_t attempt_sequence, calibration_version;
    int32_t grams;
    uint8_t port_no, status;
} ScaleReaderObservation;

uint16_t ScaleReader_Crc16(const uint8_t *bytes, uint8_t length);
/* Modbus slave 1, function 03, two registers, low word first. No calibration,
 * zero clamping or smoothing. Output is changed only on a valid response. */
uint8_t ScaleReader_Decode(const uint8_t *frame, uint8_t length,
    int32_t minimum_grams, int32_t maximum_grams, int32_t *grams);
#endif
