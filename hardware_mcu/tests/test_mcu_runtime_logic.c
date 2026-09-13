#include <assert.h>

#define ECOBIN_MCU_RUNTIME_INCLUDE_WEIGHT
#include "mcu_runtime_logic.h"

static void test_negative_zero_drift_is_normalized_to_zero(void)
{
    static const unsigned char samples[][4] = {
        {0xFFU, 0xF7U, 0xFFU, 0xFFU}, /* -9 g */
        {0xFFU, 0xDFU, 0xFFU, 0xFFU}, /* -33 g */
        {0xFFU, 0xBBU, 0xFFU, 0xFFU}  /* -69 g */
    };
    unsigned long weight;
    unsigned char i;

    for(i = 0U; i < 3U; i++)
    {
        weight = 123U;
        assert(McuRuntime_DecodeScaleWeight(
                   samples[i][0], samples[i][1],
                   samples[i][2], samples[i][3],
                   &weight) == MCU_WEIGHT_READ_OK);
        assert(weight == 0UL);
    }
}

static void test_valid_scale_weights_preserve_word_order(void)
{
    unsigned long weight = 1UL;

    assert(McuRuntime_DecodeScaleWeight(
               0x00U, 0x00U, 0x00U, 0x00U,
               &weight) == MCU_WEIGHT_READ_OK);
    assert(weight == 0UL);

    assert(McuRuntime_DecodeScaleWeight(
               0x01U, 0xF4U, 0x00U, 0x00U,
               &weight) == MCU_WEIGHT_READ_OK);
    assert(weight == 500UL);

    assert(McuRuntime_DecodeScaleWeight(
               0x57U, 0x30U, 0x00U, 0x05U,
               &weight) == MCU_WEIGHT_READ_OK);
    assert(weight == MCU_WEIGHT_MAX_GRAMS);
}

static void test_positive_overload_is_invalid_instead_of_saturated(void)
{
    unsigned long weight = 123UL;

    assert(McuRuntime_DecodeScaleWeight(
               0x57U, 0x31U, 0x00U, 0x05U,
               &weight) == MCU_WEIGHT_READ_RANGE_ERROR);
    assert(weight == 0UL);
}

static void test_complete_modbus_response_wins_over_elapsed_timeout(void)
{
    /*
     * F0 may block the main loop for longer than the two-tick scale timeout.
     * Bytes still arrive in the USART2 ISR, so a complete response must be
     * decoded instead of being discarded merely because polling was delayed.
     */
    assert(McuRuntime_WeightPollDecision(9U, 200U) ==
           MCU_WEIGHT_POLL_DATA_READY);
    assert(McuRuntime_WeightPollDecision(10U, 65531U) ==
           MCU_WEIGHT_POLL_DATA_READY);
    assert(McuRuntime_WeightPollDecision(8U, 199U) ==
           MCU_WEIGHT_POLL_WAITING);
    assert(McuRuntime_WeightPollDecision(8U, 200U) ==
           MCU_WEIGHT_POLL_TIMEOUT);
}

int main(void)
{
    test_negative_zero_drift_is_normalized_to_zero();
    test_valid_scale_weights_preserve_word_order();
    test_positive_overload_is_invalid_instead_of_saturated();
    test_complete_modbus_response_wins_over_elapsed_timeout();
    return 0;
}
