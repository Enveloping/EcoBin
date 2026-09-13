#ifndef ECOBIN_RUNTIME_CLOCK_H
#define ECOBIN_RUNTIME_CLOCK_H
#include <stdint.h>

#define RUNTIME_CLOCK_TICK_MS 10U
void RuntimeClock_Init(void);
void RuntimeClock_Advance(uint32_t elapsed_ms);
uint32_t RuntimeClock_Now(void);
/* Caller MUST mask the clock interrupt (or run in that ISR after Advance).
 * Non-atomic wide read on Cortex-M3; never use from a preempting ISR.
 * Advance has one writer; Init only at boot. Low-word API stays wrap-safe.
 */
uint64_t RuntimeClock_Now64Locked(void);
/* Intervals must be nonzero and shorter than half the 32-bit clock range. */
uint8_t RuntimeClock_PeriodDue(uint32_t now, uint32_t *last, uint32_t period_ms);
#endif
