#include "ultrasonic_reader.h"
#include "runtime_clock.h"
#include <stddef.h>
#include <string.h>

#define PHASE_TRIGGER 1u
#define PHASE_RISE 2u
#define PHASE_FALL 3u
static UltrasonicHardware io;
static volatile UltrasonicObservation observation;
static volatile uint32_t sequence, triggered_us, echo_started_us, rise_us, timeout_us;
static volatile uint8_t initialized, phase, held;
static const void *reservation;

uint8_t UltrasonicReader_Init(const UltrasonicHardware *hardware) {
    if (hardware == NULL || hardware->enter_critical == NULL || hardware->leave_critical == NULL
        || hardware->now_us == NULL || hardware->read_echo == NULL || hardware->write_trigger == NULL
        || hardware->arm_timer == NULL || hardware->cancel_timer == NULL) return 0u;
    io = *hardware;
    sequence = triggered_us = echo_started_us = rise_us = timeout_us = 0u;
    phase = held = 0u;
    reservation = NULL;
    memset((void *)&observation, 0, sizeof(observation));
    io.cancel_timer();
    io.write_trigger(0u);
    initialized = 1u;
    return 1u;
}

static void finish(uint8_t status, uint32_t pulse) {
    io.cancel_timer();
    io.write_trigger(0u);
    observation.attempt_sequence = sequence;
    observation.captured_ms = RuntimeClock_Now64Locked();
    observation.pulse_us = status == ULTRASONIC_VALID ? pulse : 0u;
    observation.status = status;
    phase = 0u;
    held = 1u;
}

uint32_t UltrasonicReader_Begin(uint32_t echo_timeout_us) {
    return UltrasonicReader_BeginOwned(NULL, echo_timeout_us);
}

uint32_t UltrasonicReader_BeginOwned(const void *owner, uint32_t echo_timeout_us) {
    uint32_t mask, now, accepted = 0u;
    if (!initialized || echo_timeout_us < 100u || echo_timeout_us > 100000u) return 0u;
    mask = io.enter_critical();
    now = io.now_us();
    if (reservation == owner && phase == 0u && !held && sequence != UINT32_MAX
        && (sequence == 0u || (uint32_t)(now - triggered_us) >= 70000u)) {
        accepted = ++sequence;
        timeout_us = echo_timeout_us;
        triggered_us = now;
        if (io.read_echo()) finish(ULTRASONIC_UNAVAILABLE, 0u);
        else {
            phase = PHASE_TRIGGER;
            io.write_trigger(1u);
            triggered_us = io.now_us();
            io.arm_timer(15u);
        }
    }
    io.leave_critical(mask);
    return accepted;
}

void UltrasonicReader_TimerIrq(void) {
    uint32_t mask, now, elapsed;
    if (!initialized) return;
    mask = io.enter_critical();
    now = io.now_us();
    if (phase == PHASE_TRIGGER) {
        elapsed = (uint32_t)(now - triggered_us);
        if (elapsed < 15u) io.arm_timer(15u - elapsed);
        else {
            io.write_trigger(0u);
            echo_started_us = now;
            if (io.read_echo()) finish(ULTRASONIC_UNAVAILABLE, 0u);
            else { phase = PHASE_RISE; io.arm_timer(timeout_us); }
        }
    } else if (phase == PHASE_RISE || phase == PHASE_FALL) {
        elapsed = (uint32_t)(now - echo_started_us);
        if (elapsed < timeout_us) io.arm_timer(timeout_us - elapsed);
        else finish(ULTRASONIC_UNAVAILABLE, 0u);
    }
    io.leave_critical(mask);
}

void UltrasonicReader_EchoIrq(void) {
    uint32_t mask, now, pulse;
    uint8_t high;
    if (!initialized) return;
    mask = io.enter_critical();
    now = io.now_us();
    high = io.read_echo();
    if (phase == PHASE_RISE || phase == PHASE_FALL) {
        if ((uint32_t)(now - echo_started_us) > timeout_us) finish(ULTRASONIC_UNAVAILABLE, 0u);
        else if (phase == PHASE_RISE && high) { rise_us = now; phase = PHASE_FALL; }
        else if (phase == PHASE_FALL && !high) {
            pulse = (uint32_t)(now - rise_us);
            finish(pulse == 0u ? ULTRASONIC_UNAVAILABLE : ULTRASONIC_VALID, pulse);
        } else finish(ULTRASONIC_UNAVAILABLE, 0u); /* Missing/coalesced/out-of-order edge. */
    }
    io.leave_critical(mask);
}

uint8_t UltrasonicReader_Copy(UltrasonicObservation *output) {
    return UltrasonicReader_CopyOwned(NULL, output);
}

uint8_t UltrasonicReader_CopyOwned(const void *owner, UltrasonicObservation *output) {
    uint32_t mask;
    uint8_t available;
    if (!initialized || output == NULL) return 0u;
    mask = io.enter_critical();
    available = (uint8_t)(reservation == owner && held);
    if (available) *output = observation;
    io.leave_critical(mask);
    return available;
}

uint8_t UltrasonicReader_Retire(uint32_t attempt_sequence) {
    return UltrasonicReader_RetireOwned(NULL, attempt_sequence);
}

uint8_t UltrasonicReader_RetireOwned(const void *owner, uint32_t attempt_sequence) {
    uint32_t mask;
    uint8_t retired = 0u;
    if (!initialized) return 0u;
    mask = io.enter_critical();
    if (reservation == owner && held && attempt_sequence != 0u && observation.attempt_sequence == attempt_sequence) {
        held = 0u;
        retired = 1u;
    }
    io.leave_critical(mask);
    return retired;
}

uint8_t UltrasonicReader_Claim(const void *owner) {
    uint32_t mask;
    uint8_t accepted = 0u;
    if (!initialized || owner == NULL) return 0u;
    mask = io.enter_critical();
    if (reservation == NULL && !phase && !held) { reservation = owner; accepted = 1u; }
    io.leave_critical(mask);
    return accepted;
}

uint8_t UltrasonicReader_Release(const void *owner) {
    uint32_t mask;
    uint8_t released = 0u;
    if (!initialized || owner == NULL) return 0u;
    mask = io.enter_critical();
    if (reservation == owner && !phase && !held) { reservation = NULL; released = 1u; }
    io.leave_critical(mask);
    return released;
}

uint8_t UltrasonicReader_CancelOwned(const void *owner) {
    uint32_t mask;
    uint8_t cancelled = 0u;
    if (!initialized || owner == NULL) return 0u;
    mask = io.enter_critical();
    if (reservation == owner && !held) {
        io.cancel_timer();
        io.write_trigger(0u);
        phase = 0u;
        cancelled = 1u;
    }
    io.leave_critical(mask);
    return cancelled;
}
