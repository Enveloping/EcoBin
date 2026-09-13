#include <assert.h>
#include "weight_measurement.h"

static const WeightMeasurementConfig config = {
    5000U, 1500U, 750U, 100U, 5U, 5U, -350000, 350000
};

int main(void)
{
    WeightMeasurement measurement;
    WeightMeasurementResult result;
    static const int32_t values[] = {1000, 1030, 980, 1020, 1010};
    uint32_t i;
    assert(WeightMeasurement_Begin(&measurement, &config, 1U, 100U, 0U));
    for(i = 0U; i < 5U; i++)
        assert(WeightMeasurement_Observe(&measurement, 1U, i + 1U,
                                        100U + i * 250U, values[i],
                                        100U + i * 250U));
    result = WeightMeasurement_Poll(&measurement, 1100U);
    assert(result.status == WEIGHT_MEASUREMENT_STABLE_MEAN);
    assert(result.value_available && result.grams == 1008);
    assert(result.sample_count == 5U && result.elapsed_ms == 1000U);

    assert(WeightMeasurement_Begin(&measurement, &config, 2U, 100U, 0U));
    for(i = 0U; i < 20U; i++)
        assert(WeightMeasurement_Observe(&measurement, 2U, i + 1U,
                                        100U + i * 250U, (int32_t)i * 101,
                                        100U + i * 250U));
    assert(WeightMeasurement_Poll(&measurement, 5099U).status == WEIGHT_MEASUREMENT_PENDING);
    result = WeightMeasurement_Poll(&measurement, 5100U);
    assert(result.status == WEIGHT_MEASUREMENT_TIMEOUT_MEDIAN);
    assert(result.value_available && result.grams == 960);
    assert(result.sample_count == 20U && result.elapsed_ms == 5000U);
    assert(!WeightMeasurement_Observe(&measurement, 2U, 21U, 5200U, 1, 5200U));
    assert(WeightMeasurement_Poll(&measurement, 99999U).grams == 960);
    /* Drain responses captured inside the deadline before finalizing, even
     * when main-loop work delays processing until after that deadline. */
    assert(WeightMeasurement_Begin(&measurement, &config, 11U, 0U, 0U));
    for(i = 0U; i < 5U; i++)
        assert(WeightMeasurement_Observe(&measurement, 11U, i + 1U,
                                        3750U + i * 250U, (int32_t)i * 101, 6000U));
    assert(!WeightMeasurement_Observe(&measurement, 11U, 6U, 5100U, 3000, 6000U));
    result = WeightMeasurement_Poll(&measurement, 6000U);
    assert(result.status == WEIGHT_MEASUREMENT_TIMEOUT_MEDIAN);
    assert(result.sample_count == 5U && result.grams == 202 && result.elapsed_ms == 5000U);
    assert(!WeightMeasurement_Observe(&measurement, 11U, 7U, 4900U, 3000, 6000U));
    /* The fifth stable sample arrived at 4.9s, and was processed at 5.1s. */
    assert(WeightMeasurement_Begin(&measurement, &config, 12U, 0U, 0U));
    for(i = 0U; i < 4U; i++)
        assert(WeightMeasurement_Observe(&measurement, 12U, i + 1U,
                                        3900U + i * 250U, values[i], 3900U + i * 250U));
    assert(WeightMeasurement_Observe(&measurement, 12U, 5U, 4900U, values[4], 5100U));
    result = WeightMeasurement_Poll(&measurement, 5100U);
    assert(result.status == WEIGHT_MEASUREMENT_STABLE_MEAN && result.grams == 1008);
    assert(result.elapsed_ms == 5000U);
    /* Signed extremes use wide accumulation and round -0.5 away from zero. */
    {
        WeightMeasurementConfig wide = config;
        wide.minimum_grams = INT32_MIN;
        wide.maximum_grams = INT32_MAX;
        assert(WeightMeasurement_Begin(&measurement, &wide, 3U, 0U, 0U));
        for(i = 0U; i < 20U; i++)
            assert(WeightMeasurement_Observe(&measurement, 3U, i + 1U, i * 250U,
                                            i % 2U ? INT32_MAX : INT32_MIN, i * 250U));
        result = WeightMeasurement_Poll(&measurement, 5000U);
        assert(result.status == WEIGHT_MEASUREMENT_TIMEOUT_MEDIAN && result.grams == -1);
        assert(result.sample_span_grams == UINT32_MAX);
    }
    /* Full span is <=100, not +/-100 and not just adjacent differences. */
    assert(WeightMeasurement_Begin(&measurement, &config, 4U, 0U, 0U));
    for(i = 0U; i < 5U; i++)
        assert(WeightMeasurement_Observe(&measurement, 4U, i + 1U, i * 250U,
                                        i == 4U ? 100 : 0, i * 250U));
    assert(WeightMeasurement_Poll(&measurement, 1000U).grams == 20);
    assert(WeightMeasurement_Begin(&measurement, &config, 5U, 0U, 0U));
    for(i = 0U; i < 5U; i++)
        assert(WeightMeasurement_Observe(&measurement, 5U, i + 1U, i * 250U,
                                        i == 4U ? 101 : 0, i * 250U));
    assert(WeightMeasurement_Poll(&measurement, 1000U).status == WEIGHT_MEASUREMENT_PENDING);
    result = WeightMeasurement_Poll(&measurement, 5000U);
    assert(result.status == WEIGHT_MEASUREMENT_UNAVAILABLE && !result.value_available);
    /* Age is evaluated at 5s: 750ms accepted, 751ms is disconnected/stale data. */
    for(i = 0U; i < 2U; i++)
    {
        uint32_t j;
        assert(WeightMeasurement_Begin(&measurement, &config, 6U, 0U, 0U));
        for(j = 0U; j < 5U; j++)
            assert(WeightMeasurement_Observe(&measurement, 6U, j + 1U,
                                            3250U - i + j * 250U, (int32_t)j * 101,
                                            3250U - i + j * 250U));
        result = WeightMeasurement_Poll(&measurement, 5000U);
        assert(result.value_available == (i == 0U));
        if(i == 0U) assert(result.grams == 202);
    }
    /* Foreign phases, cached sequences, pre-phase/future and out-of-range data. */
    assert(WeightMeasurement_Begin(&measurement, &config, 7U, 100U, 50U));
    assert(!WeightMeasurement_Observe(&measurement, 6U, 51U, 100U, 10, 100U));
    assert(!WeightMeasurement_Observe(&measurement, 7U, 50U, 100U, 10, 100U));
    assert(!WeightMeasurement_Observe(&measurement, 7U, 51U, 99U, 10, 100U));
    assert(!WeightMeasurement_Observe(&measurement, 7U, 51U, 101U, 10, 100U));
    assert(!WeightMeasurement_Observe(&measurement, 7U, 51U, 100U, 350001, 100U));
    assert(WeightMeasurement_Poll(&measurement, 5100U).sample_count == 0U);
    /* Zero is a value; startup counter rollover does not mix measurement ages. */
    assert(WeightMeasurement_Begin(&measurement, &config, 8U, UINT32_MAX - 99U, 0U));
    for(i = 0U; i < 5U; i++)
    {
        uint32_t now = UINT32_MAX - 99U + i * 250U;
        assert(WeightMeasurement_Observe(&measurement, 8U, i + 1U, now, 0, now));
    }
    result = WeightMeasurement_Poll(&measurement, 1000U);
    assert(result.value_available && result.grams == 0 && result.elapsed_ms == 1000U);
    /* Bounded storage: programming/transport bursts never overwrite samples. */
    assert(WeightMeasurement_Begin(&measurement, &config, 9U, 0U, 0U));
    for(i = 0U; i < WEIGHT_MEASUREMENT_CAPACITY; i++)
        assert(WeightMeasurement_Observe(&measurement, 9U, i + 1U, i, (int32_t)i * 101, i));
    assert(!WeightMeasurement_Observe(&measurement, 9U, 33U, 32U, 4000, 32U));
    assert(WeightMeasurement_Poll(&measurement, 33U).status == WEIGHT_MEASUREMENT_BUFFER_FULL);
    {
        WeightMeasurementConfig bad = config;
        bad.timeout_ms = 6000U;
        assert(!WeightMeasurement_Begin(&measurement, &bad, 10U, 0U, 0U));
        assert(WeightMeasurement_Poll(&measurement, 0U).status == WEIGHT_MEASUREMENT_CONFIG_ERROR);
    }
    return 0;
}
