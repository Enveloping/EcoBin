/* Register/IRQ boundary for executing the REAL STM32 adapter on the host. */
#ifndef ECOBIN_FAKE_ULTRASONIC_STM32_H
#define ECOBIN_FAKE_ULTRASONIC_STM32_H
#define __STM32F10x_H
#define __STM32F10x_EXTI_H
#define __STM32F10x_TIM_H
#include <stdint.h>
typedef struct { uint32_t CR1, CR2, SMCR, DIER, CCMR1, CCER, PSC, ARR, CNT, EGR, SR, CCR1; } TIM_TypeDef;
typedef struct { uint32_t CRH, IDR, BSRR, BRR; } TestGPIO;
typedef struct { uint32_t APB2ENR, APB1ENR; } TestRCC;
typedef struct { uint32_t EXTICR[4]; } TestAFIO;
typedef struct { uint32_t IMR, RTSR, FTSR, PR; } TestEXTI;
extern TIM_TypeDef test_tim4;
extern TestGPIO test_gpioa;
extern TestRCC test_rcc;
extern TestAFIO test_afio;
extern TestEXTI test_exti;
#define TIM4 (&test_tim4)
#define GPIOA (&test_gpioa)
#define RCC (&test_rcc)
#define AFIO (&test_afio)
#define EXTI (&test_exti)
#define TIM_SR_UIF 1u
#define TIM_SR_CC1IF 2u
#define TIM_DIER_UIE 1u
#define TIM_DIER_CC1IE 2u
#define TIM_IT_Update 1u
#define TIM_IT_CC1 2u
#define TIM_EGR_UG 1u
#define TIM_CR1_CEN 1u
#define EXTI_Line12 (1u << 12)
#define RCC_APB2ENR_AFIOEN 1u
#define RCC_APB2ENR_IOPAEN 4u
#define RCC_APB1ENR_TIM4EN 4u
#define TIM4_IRQn 30
#define EXTI15_10_IRQn 40
uint32_t __get_PRIMASK(void);
void __disable_irq(void);
void __set_PRIMASK(uint32_t mask);
void TIM_ClearITPendingBit(TIM_TypeDef *timer, uint16_t bits);
void EXTI_ClearITPendingBit(uint32_t bits);
void NVIC_ClearPendingIRQ(int irq);
void NVIC_SetPriority(int irq, uint32_t priority);
void NVIC_EnableIRQ(int irq);
#endif
