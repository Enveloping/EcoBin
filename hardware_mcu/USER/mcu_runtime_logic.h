#ifndef __ECOBIN_MCU_RUNTIME_LOGIC_H
#define __ECOBIN_MCU_RUNTIME_LOGIC_H
#include <stdint.h>

/* Fixed-frame weight fields are unsigned grams in the range 0..350000. */
#define MCU_WEIGHT_MAX_GRAMS          350000UL
#define MCU_WEIGHT_READ_OK            0U
#define MCU_WEIGHT_READ_RANGE_ERROR   4U
#define MCU_WEIGHT_RESPONSE_LENGTH    9U
#define MCU_WEIGHT_TIMEOUT_MS         200U
#define MCU_WEIGHT_POLL_DATA_READY    0U
#define MCU_WEIGHT_POLL_TIMEOUT       1U
#define MCU_WEIGHT_POLL_WAITING       2U

#define MCU_DIRECTION_STOP            0U
#define MCU_DIRECTION_CLOSE           1U
#define MCU_DIRECTION_OPEN            2U

/*
 * The deployed Modbus scale returns a signed 32-bit gram value with the low
 * 16-bit register first. A negative reading is ordinary zero drift: physical
 * total weight cannot be negative, so expose it to the fixed-frame protocol
 * as 0 g. Positive overload must stay invalid rather than being saturated.
 */
#if defined(ECOBIN_MCU_RUNTIME_INCLUDE_WEIGHT) || defined(ECOBIN_MCU_RUNTIME_INCLUDE_WEIGHT_POLL)
/*
 * USART2 continues receiving bytes in its interrupt while the main loop is
 * busy (for example, while an F0 query performs blocking ultrasonic ranging).
 * A complete Modbus response is therefore stronger evidence than an elapsed
 * polling deadline and must be decoded before considering a timeout.
 */
static unsigned char McuRuntime_WeightPollDecision(
    unsigned char received_length,
    uint32_t elapsed_ms)
{
    if(received_length >= MCU_WEIGHT_RESPONSE_LENGTH)
        return MCU_WEIGHT_POLL_DATA_READY;
    if(elapsed_ms >= MCU_WEIGHT_TIMEOUT_MS)
        return MCU_WEIGHT_POLL_TIMEOUT;
    return MCU_WEIGHT_POLL_WAITING;
}
#endif

#ifdef ECOBIN_MCU_RUNTIME_INCLUDE_WEIGHT
static unsigned char McuRuntime_DecodeScaleWeight(
    unsigned char low_word_high,
    unsigned char low_word_low,
    unsigned char high_word_high,
    unsigned char high_word_low,
    unsigned long *weight)
{
    unsigned long raw;

    raw = ((unsigned long)high_word_high << 24) |
          ((unsigned long)high_word_low << 16) |
          ((unsigned long)low_word_high << 8) |
          (unsigned long)low_word_low;

    if((raw & 0x80000000UL) != 0UL)
    {
        *weight = 0UL;
        return MCU_WEIGHT_READ_OK;
    }
    if(raw > MCU_WEIGHT_MAX_GRAMS)
    {
        *weight = 0UL;
        return MCU_WEIGHT_READ_RANGE_ERROR;
    }

    *weight = raw;
    return MCU_WEIGHT_READ_OK;
}
#endif

#endif /* __ECOBIN_MCU_RUNTIME_LOGIC_H */
