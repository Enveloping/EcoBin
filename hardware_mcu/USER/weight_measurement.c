#include "weight_measurement.h"
#include <string.h>

static int32_t rounded_divide(int64_t sum, uint8_t count)
{
    if(sum < 0)
        return (int32_t)(-((-sum + count / 2U) / count));
    return (int32_t)((sum + count / 2U) / count);
}

static uint32_t span(const WeightMeasurement *m, uint8_t first)
{
    int32_t low = m->samples[first], high = low;
    uint8_t i;
    for(i = first; i < m->count; i++)
    {
        if(m->samples[i] < low) low = m->samples[i];
        if(m->samples[i] > high) high = m->samples[i];
    }
    return (uint32_t)((int64_t)high - low);
}

uint8_t WeightMeasurement_ConfigValid(const WeightMeasurementConfig *config)
{
    if(config == 0 || config->timeout_ms == 0U ||
       config->timeout_ms > 5000U || config->stable_window_ms == 0U ||
       config->stable_window_ms > config->timeout_ms ||
       config->maximum_age_ms == 0U || config->maximum_age_ms > config->timeout_ms ||
       config->stable_samples == 0U || config->stable_samples > WEIGHT_MEASUREMENT_CAPACITY ||
       config->minimum_median_samples == 0U ||
       config->minimum_median_samples > WEIGHT_MEASUREMENT_CAPACITY ||
        config->minimum_grams > config->maximum_grams)
        return 0U;
    return 1U;
}

uint8_t WeightMeasurement_Begin(WeightMeasurement *m,
    const WeightMeasurementConfig *config, uint32_t measurement_id,
    uint32_t now, uint32_t after_sample_id)
{
    memset(m, 0, sizeof(*m));
    m->result.measurement_id = measurement_id;
    m->result.status = WEIGHT_MEASUREMENT_CONFIG_ERROR;
    if(measurement_id == 0U || !WeightMeasurement_ConfigValid(config))
        return 0U;
    m->config = *config;
    m->started_ms = now;
    m->last_sample_id = after_sample_id;
    m->result.status = WEIGHT_MEASUREMENT_PENDING;
    return 1U;
}

uint8_t WeightMeasurement_Observe(WeightMeasurement *m,
    uint32_t measurement_id, uint32_t sample_id, uint32_t received_ms,
    int32_t grams, uint32_t now)
{
    uint32_t elapsed = (uint32_t)(now - m->started_ms);
    uint32_t received_elapsed = (uint32_t)(received_ms - m->started_ms);
    uint8_t first, i;
    int64_t sum = 0;
    if(m->result.status != WEIGHT_MEASUREMENT_PENDING)
        return 0U;
    if(measurement_id != m->result.measurement_id || sample_id <= m->last_sample_id ||
       received_elapsed > elapsed || received_elapsed > m->config.timeout_ms ||
       grams < m->config.minimum_grams ||
       grams > m->config.maximum_grams ||
       (m->count && received_elapsed < (uint32_t)(m->received_ms[m->count - 1U] - m->started_ms)))
        return 0U;
    /* Drain captured in-window responses before Poll freezes the result.
     * Processing delay cannot extend the acquisition window or data freshness. */
    if(elapsed > m->config.timeout_ms) elapsed = m->config.timeout_ms;
    if(m->count == WEIGHT_MEASUREMENT_CAPACITY)
    {
        m->result.status = WEIGHT_MEASUREMENT_BUFFER_FULL;
        m->result.elapsed_ms = elapsed;
        return 0U;
    }
    m->last_sample_id = sample_id;
    m->received_ms[m->count] = received_ms;
    m->samples[m->count++] = grams;
    m->result.sample_count = m->count;
    if(m->count < m->config.stable_samples)
        return 1U;
    first = (uint8_t)(m->count - m->config.stable_samples);
    if((uint32_t)(received_ms - m->received_ms[first]) > m->config.stable_window_ms ||
       elapsed - received_elapsed > m->config.maximum_age_ms ||
       span(m, first) > m->config.maximum_span_grams)
        return 1U;
    for(i = first; i < m->count; i++) sum += m->samples[i];
    m->result.grams = rounded_divide(sum, m->config.stable_samples);
    m->result.sample_count = m->config.stable_samples;
    m->result.sample_span_grams = span(m, first);
    m->result.elapsed_ms = elapsed;
    m->result.value_available = 1U;
    m->result.status = WEIGHT_MEASUREMENT_STABLE_MEAN;
    return 1U;
}

WeightMeasurementResult WeightMeasurement_Poll(WeightMeasurement *m, uint32_t now)
{
    int32_t sorted[WEIGHT_MEASUREMENT_CAPACITY];
    int32_t value;
    int64_t middle;
    uint8_t i, j;
    if(m->result.status == WEIGHT_MEASUREMENT_PENDING &&
       (uint32_t)(now - m->started_ms) >= m->config.timeout_ms)
    {
        m->result.elapsed_ms = m->config.timeout_ms;
        m->result.status = WEIGHT_MEASUREMENT_UNAVAILABLE;
        /* Freshness is evaluated at the measurement deadline, not upload time. */
        if(m->count >= m->config.minimum_median_samples &&
           (uint32_t)(m->started_ms + m->config.timeout_ms -
                      m->received_ms[m->count - 1U]) <= m->config.maximum_age_ms)
        {
            for(i = 0U; i < m->count; i++) sorted[i] = m->samples[i];
            for(i = 1U; i < m->count; i++)
            {
                value = sorted[i];
                j = i;
                while(j > 0U && sorted[j - 1U] > value)
                {
                    sorted[j] = sorted[j - 1U];
                    j--;
                }
                sorted[j] = value;
            }
            middle = sorted[m->count / 2U];
            if(m->count % 2U == 0U)
                middle += sorted[m->count / 2U - 1U];
            m->result.grams = rounded_divide(middle, (m->count % 2U) ? 1U : 2U);
            m->result.sample_span_grams = span(m, 0U);
            m->result.value_available = 1U;
            m->result.status = WEIGHT_MEASUREMENT_TIMEOUT_MEDIAN;
        }
    }
    return m->result;
}

WeightMeasurementResult WeightMeasurement_Interrupt(WeightMeasurement *m, uint32_t now)
{
    WeightMeasurement_Poll(m, now);
    if(m->result.status == WEIGHT_MEASUREMENT_PENDING)
    {
        m->result.status = WEIGHT_MEASUREMENT_INTERRUPTED;
        m->result.elapsed_ms = (uint32_t)(now - m->started_ms);
        m->result.value_available = 0U;
        m->result.grams = 0;
        m->result.sample_span_grams = 0U;
    }
    return m->result;
}
