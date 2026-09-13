#ifndef __ADC_H
#define __ADC_H
#include "stm32f10x.h"

void  ADC1_Init(void);
unsigned char ADC1_TryInit(void); /* Optional ADC failure is reported, never an infinite boot wait. */
u16   ADC1_Read(void);          /* 读PA4 ADC值 (阻塞, 旧版兼容) */
unsigned char ADC1_TryRead(u16 *value); /* 带成功标志: 返回1=成功 0=超时, value=0~4095 */
float Smoke_GetVoltage(void);   /* 读烟雾传感器电压 */

#endif
