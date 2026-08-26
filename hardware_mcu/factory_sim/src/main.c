#include "factory_sim.h"

#include <stdint.h>

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))

#define RCC_CR REG32(0x40021000u)
#define RCC_CFGR REG32(0x40021004u)
#define RCC_APB2ENR REG32(0x40021018u)
#define GPIOA_CRH REG32(0x40010804u)
#define GPIOA_ODR REG32(0x4001080Cu)
#define USART1_SR REG32(0x40013800u)
#define USART1_DR REG32(0x40013804u)
#define USART1_BRR REG32(0x40013808u)
#define USART1_CR1 REG32(0x4001380Cu)
#define NVIC_ISER1 REG32(0xE000E104u)
#define SYSTICK_CTRL REG32(0xE000E010u)
#define SYSTICK_LOAD REG32(0xE000E014u)
#define SYSTICK_VAL REG32(0xE000E018u)

#define UART_RING_CAPACITY 256u
#define UART_RING_MASK (UART_RING_CAPACITY - 1u)

static FactorySim g_sim;
static volatile uint32_t g_millis;
static volatile uint16_t g_rx_head;
static volatile uint16_t g_rx_tail;
static volatile uint32_t g_rx_overflow_count;
static uint8_t g_rx_ring[UART_RING_CAPACITY];

static void clock_init_hsi_8mhz(void)
{
    RCC_CR |= 1u;
    while ((RCC_CR & (1u << 1)) == 0u) {
    }
    RCC_CFGR &= ~0x3u;
    while ((RCC_CFGR & (0x3u << 2)) != 0u) {
    }
}
static void usart1_init(void)
{
    /* AFIO, GPIOA and USART1 clocks. No sensor/actuator peripheral is enabled. */
    RCC_APB2ENR |= (1u << 0) | (1u << 2) | (1u << 14);

    /* PA9: alternate-function push-pull 50 MHz; PA10: input pull-up. */
    GPIOA_CRH = (GPIOA_CRH & ~((0xFu << 4) | (0xFu << 8))) |
                (0xBu << 4) | (0x8u << 8);
    GPIOA_ODR |= (1u << 10);

    /* PCLK2=8 MHz, USARTDIV*16 rounded to 69 -> about 115942 baud. */
    USART1_BRR = 69u;
    USART1_CR1 = (1u << 13) | (1u << 5) | (1u << 3) | (1u << 2);
    NVIC_ISER1 = (1u << (37u - 32u));
}

static void systick_init(void)
{
    SYSTICK_LOAD = 7999u;
    SYSTICK_VAL = 0u;
    SYSTICK_CTRL = 0x07u; /* processor clock, interrupt, enable */
}

static void uart_tx_byte(uint8_t value, void *user)
{
    (void)user;
    while ((USART1_SR & (1u << 7)) == 0u) {
    }
    USART1_DR = value;
}

static uint8_t uart_rx_pop(uint8_t *value)
{
    const uint16_t tail = g_rx_tail;

    if (tail == g_rx_head) {
        return 0u;
    }
    *value = g_rx_ring[tail & UART_RING_MASK];
    g_rx_tail = (uint16_t)(tail + 1u);
    return 1u;
}

void SysTick_Handler(void)
{
    ++g_millis;
}

void USART1_IRQHandler(void)
{
    const uint32_t status = USART1_SR;

    if ((status & (1u << 5)) != 0u) {
        const uint8_t value = (uint8_t)USART1_DR;
        const uint16_t head = g_rx_head;

        if ((uint16_t)(head - g_rx_tail) < UART_RING_CAPACITY) {
            g_rx_ring[head & UART_RING_MASK] = value;
            g_rx_head = (uint16_t)(head + 1u);
        } else {
            ++g_rx_overflow_count;
        }
    } else if ((status & (1u << 3)) != 0u) {
        (void)USART1_DR; /* clear a possible overrun */
    }
}

int main(void)
{
    uint8_t value;

    clock_init_hsi_8mhz();
    usart1_init();
    systick_init();
    factory_sim_init(&g_sim, uart_tx_byte, (void *)0);

    for (;;) {
        while (uart_rx_pop(&value) != 0u) {
            const uint32_t now_ms = g_millis;
            factory_sim_feed(&g_sim, &value, 1u, now_ms);
        }
        factory_sim_poll(&g_sim, g_millis);
        __asm volatile("wfi");
    }
}
