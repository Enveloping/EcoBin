#ifndef __SMOKE_MONITOR_H
#define __SMOKE_MONITOR_H

/* Smoke state codes (protocol CC frame) */
#define SMOKE_NORMAL     0x00  /* sensor OK, no alarm */
#define SMOKE_ALARM      0x01  /* sensor OK, alarm triggered */
#define SMOKE_UNAVAIL    0x02  /* sensor unavailable (warmup or HW fail) */

/* Warmup: MQ-2 needs ~60s preheat */
#define SMOKE_WARMUP_MS  60000

void SmokeMonitor_Init(void);
void SmokeMonitor_Update(void);                   /* call every 50ms in main loop */
/* Same real sampling/debounce, returns 1 only if one ADC attempt completed.
 * A repeated poll or preheat wait is not a new observation. */
unsigned char SmokeMonitor_UpdateSample(void);
unsigned char SmokeMonitor_GetState(void);        /* current stable state */
unsigned char SmokeMonitor_PollChanged(void);     /* 1=state changed since last poll */

#endif
