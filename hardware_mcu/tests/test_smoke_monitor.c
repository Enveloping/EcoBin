#include <assert.h>
#include <stdint.h>
#include "runtime_clock.h"
#include "smoke_monitor.h"

static unsigned int reads;
static u16 adc_value;
static unsigned char adc_ok = 1;

unsigned char ADC1_TryRead(u16 *value)
{
    reads++;
    *value = adc_value;
    return adc_ok;
}

static void sample(void)
{
    RuntimeClock_Advance(50U);
    SmokeMonitor_Update();
}

int main(void)
{
    unsigned int i;
    RuntimeClock_Init();
    SmokeMonitor_Init();
    adc_value = 2000U;
    RuntimeClock_Advance(59950U);
    SmokeMonitor_Update();
    assert(reads == 0U && SmokeMonitor_GetState() == SMOKE_UNAVAIL);
    sample();
    assert(reads == 1U && SmokeMonitor_GetState() == SMOKE_UNAVAIL);
    sample();
    sample();
    assert(SmokeMonitor_GetState() == SMOKE_ALARM);
    assert(SmokeMonitor_PollChanged() && !SmokeMonitor_PollChanged());

    /* A continuously running sensor must never re-enter startup warmup. */
    RuntimeClock_Advance(UINT32_MAX - RuntimeClock_Now());
    SmokeMonitor_Update();
    RuntimeClock_Advance(51U);
    SmokeMonitor_Update();
    assert(SmokeMonitor_GetState() == SMOKE_ALARM);
    assert(!SmokeMonitor_PollChanged());

    /* A blocked main loop contributes one real sample, not replayed ticks. */
    adc_value = 0U;
    reads = 0U;
    RuntimeClock_Advance(5000U);
    SmokeMonitor_Update();
    for(i = 0U; i < 100U; i++) SmokeMonitor_Update();
    assert(reads == 1U && SmokeMonitor_GetState() == SMOKE_ALARM);
    for(i = 0U; i < 19U; i++) sample();
    assert(reads == 20U && SmokeMonitor_GetState() == SMOKE_NORMAL);
    assert(SmokeMonitor_PollChanged());
    adc_ok = 0U;
    for(i = 0U; i < 10U; i++) sample();
    assert(SmokeMonitor_GetState() == SMOKE_UNAVAIL);

    /* Initialization close to rollover still gets exactly one warmup period. */
    RuntimeClock_Init();
    RuntimeClock_Advance(UINT32_MAX - 99U);
    SmokeMonitor_Init();
    reads = 0U;
    adc_ok = 1U;
    RuntimeClock_Advance(59950U);
    SmokeMonitor_Update();
    assert(reads == 0U);
    sample();
    assert(reads == 1U);
    for(i = 0U; i < 19U; i++) sample();
    assert(SmokeMonitor_GetState() == SMOKE_NORMAL);
    return 0;
}
