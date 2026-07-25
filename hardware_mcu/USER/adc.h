#ifndef __ADC_H
#define __ADC_H
#include "stm32f10x.h"

void  ADC1_Init(void);
u16   ADC1_Read(void);          /* 读PA4 ADC值 */
float Smoke_GetVoltage(void);   /* 读烟雾传感器电压 */

#endif
