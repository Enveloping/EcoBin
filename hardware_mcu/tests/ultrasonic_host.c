/* Test-only GPIO and one-shot timer boundary. No sensor state machine here. */
#include "ultrasonic_reader.h"
#include "runtime_clock.h"
static uint64_t now_us, due;
static uint32_t mask;
static uint32_t expire_after, expire_delta;
static uint8_t trigger, echo, armed;
void TestUltrasonic_Advance(uint32_t delta);
static uint32_t enter(void) {
    uint32_t previous = mask;
    /* Simulate timer IRQ just before entering a hardware critical section. */
    if (expire_after && --expire_after == 0u) TestUltrasonic_Advance(expire_delta);
    mask = 1u;
    return previous;
}
static void leave(uint32_t previous) { mask = previous; }
static uint32_t now(void) { return (uint32_t)now_us; }
static uint8_t input(void) { return echo; }
static void output(uint8_t value) { trigger = value; }
static void arm(uint32_t delay) { due = now_us + delay; armed = 1u; }
static void cancel(void) { armed = 0u; }
static const UltrasonicHardware hardware = {enter, leave, now, input, output, arm, cancel};
void TestUltrasonic_Init(void) {
    now_us = due = 0u; mask = expire_after = expire_delta = 0u; trigger = echo = armed = 0u;
    UltrasonicReader_Init(&hardware);
}
static void advance_to(uint64_t at) {
    RuntimeClock_Advance((uint32_t)(at / 1000u - now_us / 1000u)); now_us = at;
}
void TestUltrasonic_Advance(uint32_t delta) {
    uint64_t target = now_us + delta;
    while (armed && due <= target) {
        advance_to(due); armed = 0u; UltrasonicReader_TimerIrq();
    }
    advance_to(target);
}
void TestUltrasonic_Edge(uint8_t value) { echo = value; UltrasonicReader_EchoIrq(); }
uint8_t TestUltrasonic_Trigger(void) { return trigger; }
void TestUltrasonic_ExpireOnEntry(uint32_t after, uint32_t delta) { expire_after = after; expire_delta = delta; }
