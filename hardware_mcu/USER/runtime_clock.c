#include "runtime_clock.h"

static volatile uint32_t now_ms;
static volatile uint32_t wraps;
void RuntimeClock_Init(void) { now_ms = 0U; wraps = 0U; }
void RuntimeClock_Advance(uint32_t elapsed_ms)
{
    uint32_t previous = now_ms;
    now_ms += elapsed_ms;
    if(now_ms < previous) wraps++;
}
uint32_t RuntimeClock_Now(void) { return now_ms; }
uint64_t RuntimeClock_Now64Locked(void)
{
    return ((uint64_t)wraps << 32) | now_ms;
}

uint8_t RuntimeClock_PeriodDue(uint32_t now, uint32_t *last, uint32_t period_ms)
{
    if(period_ms == 0U || period_ms >= 0x80000000UL)
        return 0U;
    if((uint32_t)(now - *last) < period_ms)
        return 0U;
    *last = now; /* Missed periods never produce a catch-up burst. */
    return 1U;
}
