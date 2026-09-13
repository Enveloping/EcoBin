/******************** (C) COPYRIGHT 2019 Designed by Captain *********************
 * smoke_monitor.c - MQ-2 smoke sensor state machine with debounce & hysteresis
 *
 * Key rules:
 *  - Only emit CC when STABLE state changes (not on raw ADC jitter)
 *  - Hysteresis: alarm at >1800, recover at <1400
 *  - Debounce: N consecutive samples required before state transition
 *  - Warmup: state=02 for ~60s after power-on
 *  - ADC value 0 is VALID (clean air), not "unavailable"
 **********************************************************************************/
#include "smoke_monitor.h"
#include "adc.h"
#include "runtime_clock.h"

/* Thresholds */
#define SMOKE_ALARM_THRESH   1800
#define SMOKE_RECOVER_THRESH 1400

/* Debounce counts (50ms per sample) */
#define DEBOUNCE_TO_ALARM    3    /* 150ms to trigger alarm */
#define DEBOUNCE_TO_NORMAL   20   /* 1s to recover from alarm */
#define DEBOUNCE_TO_UNAVAIL  10   /* 500ms ADC fail -> unavailable */
#define DEBOUNCE_TO_AVAIL    10   /* 500ms ADC ok -> available */

static unsigned char stable_state = SMOKE_UNAVAIL;  /* power-on = warmup */
static unsigned char candidate_state = SMOKE_UNAVAIL;
static unsigned char candidate_count = 0;
static unsigned char changed_flag = 0;
static uint32_t warmup_started_ms;
static unsigned char warmup_complete;
static uint32_t last_sample_ms;

void SmokeMonitor_Init(void)
{
    stable_state = SMOKE_UNAVAIL;
    candidate_state = SMOKE_UNAVAIL;
    candidate_count = 0;
    changed_flag = 0;
    warmup_started_ms = RuntimeClock_Now();
    warmup_complete = 0;
    last_sample_ms = warmup_started_ms;
}

void SmokeMonitor_Update(void)
{
    (void)SmokeMonitor_UpdateSample();
}

unsigned char SmokeMonitor_UpdateSample(void)
{
    unsigned char raw_state;
    u16 adc_val;
    unsigned char read_ok;
    uint32_t now = RuntimeClock_Now();

    /* One real ADC sample per elapsed interval, never replay missed samples. */
    if(!RuntimeClock_PeriodDue(now, &last_sample_ms, 50U))
        return 0;

    /* Warmup timer */
    if(!warmup_complete)
    {
        if((uint32_t)(now - warmup_started_ms) < SMOKE_WARMUP_MS)
            return 0;
        /* Startup warmup happens once, including across 32-bit clock wrap. */
        warmup_complete = 1;
    }

    /* Read ADC with success flag */
    read_ok = ADC1_TryRead(&adc_val);

    if(!read_ok)
    {
        /* ADC hardware failure: candidate = unavailable */
        raw_state = SMOKE_UNAVAIL;
    }
    else if(adc_val > SMOKE_ALARM_THRESH)
    {
        /* Above alarm threshold */
        raw_state = SMOKE_ALARM;
    }
    else if(stable_state == SMOKE_ALARM && adc_val > SMOKE_RECOVER_THRESH)
    {
        /* Hysteresis: still in alarm zone, don't recover yet */
        raw_state = SMOKE_ALARM;
    }
    else
    {
        /* Normal (ADC value 0 is valid clean air) */
        raw_state = SMOKE_NORMAL;
    }

    /* Debounce: accumulate consecutive same-candidate samples */
    if(raw_state == candidate_state)
    {
        candidate_count++;
    }
    else
    {
        candidate_state = raw_state;
        candidate_count = 1;
    }

    /* Check if candidate has met debounce threshold */
    {
        unsigned char required = 0;

        if(candidate_state != stable_state)
        {
            switch(candidate_state)
            {
                case SMOKE_ALARM:   required = DEBOUNCE_TO_ALARM;   break;
                case SMOKE_NORMAL:  required = DEBOUNCE_TO_NORMAL;  break;
                case SMOKE_UNAVAIL: required = DEBOUNCE_TO_UNAVAIL; break;
            }
        }

        if(required > 0 && candidate_count >= required)
        {
            stable_state = candidate_state;
            changed_flag = 1;
            candidate_count = 0;
        }
    }
    return 1;
}

unsigned char SmokeMonitor_GetState(void)
{
    return stable_state;
}

unsigned char SmokeMonitor_PollChanged(void)
{
    if(changed_flag)
    {
        changed_flag = 0;
        return 1;
    }
    return 0;
}
