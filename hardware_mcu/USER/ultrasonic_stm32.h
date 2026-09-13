#ifndef ECOBIN_ULTRASONIC_STM32_H
#define ECOBIN_ULTRASONIC_STM32_H
#include <stdint.h>
/* Native-mode boot ONLY: owns PA11/TRIG, PA12/ECHO, EXTI12 and TIM4.
 * Do not call legacy HCSR04_Init/GetDistance after this or share TIM4.
 * 72 MHz TIM4 source is the current board clock; no actuator pins are touched.
 * IRQ service/critical sections must stay below one 65536 us timer wrap.
 * No main-mode switch is performed here. Call before admitting native work. */
uint8_t UltrasonicStm32_Init(void);
void TIM4_IRQHandler(void);
void EXTI15_10_IRQHandler(void);
#endif
