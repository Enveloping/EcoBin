#include "ultrasonic_stm32.h"
#include "ultrasonic_reader.h"
#include "stm32f10x.h"
#include "stm32f10x_exti.h"
#include "stm32f10x_tim.h"

static volatile uint32_t timer_high, timer_deadline;
static volatile uint8_t timer_armed, initialized;
static uint32_t enter(void) { uint32_t mask = __get_PRIMASK(); __disable_irq(); return mask; }
static void leave(uint32_t mask) { __set_PRIMASK(mask); }
static uint32_t now(void) {
    uint32_t upper = timer_high;
    uint16_t lower = (uint16_t)TIM4->CNT;
    /* Called masked. Include an overflow not yet serviced by TIM4 IRQ without
     * consuming it; re-read CNT in that epoch. Never infer two missed wraps. */
    if (TIM4->SR & TIM_SR_UIF) { upper += 65536u; lower = (uint16_t)TIM4->CNT; }
    return upper + lower;
}
static uint8_t input(void) { return (uint8_t)((GPIOA->IDR & (1u << 12)) != 0u); }
static void output(uint8_t high) {
    if (high) GPIOA->BSRR = 1u << 11;
    else GPIOA->BRR = 1u << 11;
}
static void cancel(void) { timer_armed = 0u; TIM4->DIER &= (uint16_t)~TIM_DIER_CC1IE; }
static void arm(uint32_t delay) {
    timer_deadline = now() + delay;
    TIM4->CCR1 = (uint16_t)timer_deadline;
    TIM_ClearITPendingBit(TIM4, TIM_IT_CC1);
    timer_armed = 1u;
    TIM4->DIER |= TIM_DIER_CC1IE;
}
static const UltrasonicHardware hardware = {enter, leave, now, input, output, arm, cancel};

uint8_t UltrasonicStm32_Init(void) {
    uint32_t mask;
    if (initialized) return 0u; /* A reconnect must not reset the attempt owner. */
    mask = enter();
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN | RCC_APB2ENR_AFIOEN;
    RCC->APB1ENR |= RCC_APB1ENR_TIM4EN;
    GPIOA->BRR = 1u << 11;
    GPIOA->BSRR = 1u << 12; /* Preserve this board's existing ECHO pull-up. */
    GPIOA->CRH = (GPIOA->CRH & ~(0xffu << 12)) | (0x83u << 12);
    AFIO->EXTICR[3] &= ~0x0fu; /* EXTI12 source = PA12; preserve lines 13..15. */
    EXTI->IMR &= ~EXTI_Line12;
    EXTI->RTSR |= EXTI_Line12;
    EXTI->FTSR |= EXTI_Line12;
    EXTI_ClearITPendingBit(EXTI_Line12);
    TIM4->CR1 = 0u;
    TIM4->CR2 = 0u;
    TIM4->SMCR = 0u;
    TIM4->DIER = 0u;
    TIM4->CCMR1 = 0u;
    TIM4->CCER = 0u;
    TIM4->PSC = 71u;
    TIM4->ARR = 65535u;
    TIM4->CNT = 0u;
    TIM4->EGR = TIM_EGR_UG;
    TIM_ClearITPendingBit(TIM4, TIM_IT_Update | TIM_IT_CC1);
    timer_high = timer_deadline = 0u;
    timer_armed = 0u;
    if (!UltrasonicReader_Init(&hardware)) { leave(mask); return 0u; }
    initialized = 1u;
    NVIC_ClearPendingIRQ(TIM4_IRQn);
    NVIC_ClearPendingIRQ(EXTI15_10_IRQn);
    NVIC_SetPriority(EXTI15_10_IRQn, 3u);
    NVIC_SetPriority(TIM4_IRQn, 4u);
    TIM4->DIER = TIM_DIER_UIE;
    TIM4->CR1 = TIM_CR1_CEN;
    EXTI->IMR |= EXTI_Line12;
    NVIC_EnableIRQ(TIM4_IRQn);
    NVIC_EnableIRQ(EXTI15_10_IRQn);
    leave(mask);
    return 1u;
}

void TIM4_IRQHandler(void) {
    uint32_t mask;
    if (!initialized) return;
    mask = enter();
    if (TIM4->SR & TIM_SR_UIF) {
        timer_high += 65536u;
        TIM_ClearITPendingBit(TIM4, TIM_IT_Update);
    }
    if (TIM4->SR & TIM_SR_CC1IF) TIM_ClearITPendingBit(TIM4, TIM_IT_CC1);
    /* A 100000 us deadline's low 16 bits match once early: retain the armed
     * compare until the actual 32-bit deadline, including timer rollover. */
    if (timer_armed && (int32_t)(now() - timer_deadline) >= 0) {
        cancel();
        UltrasonicReader_TimerIrq();
    }
    leave(mask);
}

void EXTI15_10_IRQHandler(void) {
    uint32_t mask;
    if (!initialized || !(EXTI->PR & EXTI_Line12)) return;
    mask = enter();
    EXTI_ClearITPendingBit(EXTI_Line12);
    UltrasonicReader_EchoIrq();
    leave(mask);
}
