#include <stdint.h>

extern uint32_t _estack;
extern uint32_t _sidata;
extern uint32_t _sdata;
extern uint32_t _edata;
extern uint32_t _sbss;
extern uint32_t _ebss;

int main(void);
void Reset_Handler(void);
void SysTick_Handler(void);
void USART1_IRQHandler(void);

static void Default_Handler(void)
{
    for (;;) {
    }
}
typedef void (*InterruptHandler)(void);

__attribute__((used, section(".isr_vector")))
const InterruptHandler g_vector_table[16u + 43u] = {
    [0] = (InterruptHandler)&_estack,
    [1] = Reset_Handler,
    [2] = Default_Handler,
    [3] = Default_Handler,
    [4] = Default_Handler,
    [5] = Default_Handler,
    [6] = Default_Handler,
    [11] = Default_Handler,
    [12] = Default_Handler,
    [14] = Default_Handler,
    [15] = SysTick_Handler,
    [16 + 37] = USART1_IRQHandler
};

void Reset_Handler(void)
{
    uint32_t *source = &_sidata;
    uint32_t *destination = &_sdata;

    while (destination < &_edata) {
        *destination++ = *source++;
    }
    destination = &_sbss;
    while (destination < &_ebss) {
        *destination++ = 0u;
    }

    *(volatile uint32_t *)(uintptr_t)0xE000ED08u =
        (uint32_t)(uintptr_t)g_vector_table;
    (void)main();
    for (;;) {
    }
}
