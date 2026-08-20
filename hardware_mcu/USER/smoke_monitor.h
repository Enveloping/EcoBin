#ifndef __SMOKE_MONITOR_H
#define __SMOKE_MONITOR_H

/* Smoke state codes (protocol CC frame) */
#define SMOKE_NORMAL     0x00  /* sensor OK, no alarm */
#define SMOKE_ALARM      0x01  /* sensor OK, alarm triggered */
#define SMOKE_UNAVAIL    0x02  /* sensor unavailable (warmup or HW fail) */

/* Warmup: MQ-2 needs ~60s preheat */
#define SMOKE_WARMUP_MS  60000

void SmokeMonitor_Init(void);
void SmokeMonitor_SetTickMs(unsigned short ms);  /* call from TIM ISR every 1ms */
void SmokeMonitor_Update(void);                   /* call every 50ms in main loop */
unsigned char SmokeMonitor_GetState(void);        /* current stable state */
unsigned char SmokeMonitor_PollChanged(void);     /* 1=state changed since last poll */

#endif
