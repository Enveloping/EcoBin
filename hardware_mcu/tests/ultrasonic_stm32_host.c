#include "fake_ultrasonic_stm32.h"
#include "ultrasonic_stm32.h"
#include "runtime_clock.h"
TIM_TypeDef test_tim4;
TestGPIO test_gpioa;
TestRCC test_rcc;
TestAFIO test_afio;
TestEXTI test_exti;
static uint32_t mask, micros;
static uint8_t trigger, deferred_timer, enabled[64], priority[64];
uint32_t __get_PRIMASK(void) { return mask; }
void __disable_irq(void) { mask = 1u; }
void __set_PRIMASK(uint32_t previous) { mask = previous; }
void TIM_ClearITPendingBit(TIM_TypeDef *timer, uint16_t bits) { timer->SR &= ~bits; }
void EXTI_ClearITPendingBit(uint32_t bits) { EXTI->PR &= ~bits; }
void NVIC_ClearPendingIRQ(int irq) { (void)irq; }
void NVIC_SetPriority(int irq, uint32_t value) { priority[irq] = (uint8_t)value; }
void NVIC_EnableIRQ(int irq) { enabled[irq] = 1u; }
static void outputs(void) {
    if (GPIOA->BSRR & (1u << 11)) trigger = 1u;
    if (GPIOA->BRR & (1u << 11)) trigger = 0u;
    GPIOA->BSRR = GPIOA->BRR = 0u;
}
void TestAdapter_Prepare(void) {
    GPIOA->CRH = 0x12345678u; AFIO->EXTICR[3] = 0x4321u;
    EXTI->IMR = 1u << 13; EXTI->PR = (1u << 13) | EXTI_Line12;
}
void TestAdapter_Advance(uint32_t us) {
    outputs();
    while (us--) {
        if ((++micros % 1000u) == 0u) RuntimeClock_Advance(1u);
        if (TIM4->CR1 & TIM_CR1_CEN) {
            TIM4->CNT = (TIM4->CNT + 1u) & 65535u;
            if (TIM4->CNT == 0u) TIM4->SR |= TIM_SR_UIF;
            if (TIM4->CNT == TIM4->CCR1) TIM4->SR |= TIM_SR_CC1IF;
        }
        if (!mask && !deferred_timer && enabled[TIM4_IRQn] && (TIM4->SR & TIM4->DIER & 3u)) TIM4_IRQHandler();
        outputs();
    }
}
void TestAdapter_Edge(uint8_t high) {
    if (high) GPIOA->IDR |= 1u << 12; else GPIOA->IDR &= ~(1u << 12);
    EXTI->PR |= EXTI_Line12;
    if (!mask && enabled[EXTI15_10_IRQn] && (EXTI->IMR & EXTI_Line12)) EXTI15_10_IRQHandler();
    outputs();
}
uint8_t TestAdapter_Trigger(void) { outputs(); return trigger; }
void TestAdapter_DeferTimer(uint8_t defer) {
    deferred_timer = defer;
    if (!defer && !mask && (TIM4->SR & TIM4->DIER & 3u)) TIM4_IRQHandler();
    outputs();
}
uint32_t TestAdapter_CheckInit(void) {
    return GPIOA->CRH == ((0x12345678u & ~(0xffu << 12)) | (0x83u << 12))
        && AFIO->EXTICR[3] == 0x4320u && EXTI->IMR == ((1u << 13) | EXTI_Line12)
        && EXTI->PR == (1u << 13) && (EXTI->RTSR & EXTI_Line12) && (EXTI->FTSR & EXTI_Line12)
        && TIM4->PSC == 71u && TIM4->ARR == 65535u && priority[EXTI15_10_IRQn] < priority[TIM4_IRQn];
}
