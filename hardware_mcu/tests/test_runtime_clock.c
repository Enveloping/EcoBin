#include <assert.h>
#include "runtime_clock.h"

int main(void)
{
    uint32_t last = 0U;
    RuntimeClock_Init();
    RuntimeClock_Advance(240U);
    assert(!RuntimeClock_PeriodDue(RuntimeClock_Now(), &last, 250U));
    RuntimeClock_Advance(10U);
    assert(RuntimeClock_PeriodDue(RuntimeClock_Now(), &last, 250U));
    RuntimeClock_Advance(900U);
    assert(RuntimeClock_PeriodDue(RuntimeClock_Now(), &last, 250U));
    assert(!RuntimeClock_PeriodDue(RuntimeClock_Now(), &last, 250U));
    last = UINT32_MAX - 99U;
    assert(RuntimeClock_PeriodDue(150U, &last, 250U));
    RuntimeClock_Init();
    RuntimeClock_Advance(UINT32_MAX - 9U);
    RuntimeClock_Advance(20U);
    assert(RuntimeClock_Now() == 10U);
    assert(RuntimeClock_Now64Locked() == ((uint64_t)UINT32_MAX + 11U));
    assert(!RuntimeClock_PeriodDue(150U, &last, 0U));
    assert(!RuntimeClock_PeriodDue(150U, &last, 0x80000000UL));
    return 0;
}
