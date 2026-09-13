#ifndef ECOBIN_ULTRASONIC_READER_H
#define ECOBIN_ULTRASONIC_READER_H
#include <stdint.h>

#define ULTRASONIC_VALID 1u
#define ULTRASONIC_UNAVAILABLE 2u
typedef struct {
    uint32_t (*enter_critical)(void);
    void (*leave_critical)(uint32_t mask);
    uint32_t (*now_us)(void);
    uint8_t (*read_echo)(void);
    void (*write_trigger)(uint8_t high);
    void (*arm_timer)(uint32_t delay_us);
    void (*cancel_timer)(void);
} UltrasonicHardware;
typedef struct {
    uint64_t captured_ms;
    uint32_t attempt_sequence, pulse_us;
    uint8_t status;
} UltrasonicObservation;

/* One physical HC-SR04 owner (PA11/PA12 on this board). Call Init once at MCU
 * boot, not Pi reconnect or each sample. The monotonic microsecond timer and
 * RuntimeClock must keep running independently of foreground work. Hardware
 * callbacks are bounded and non-reentrant; no blocking acquisition is allowed.
 * IRQ latency/edge loss remain physical HIL checks, not proven by host tests. */
uint8_t UltrasonicReader_Init(const UltrasonicHardware *hardware);
/* Caller supplies the verified configuration's actual echo timeout. Starts one
 * pulse, never an automatic sample loop or retry. 0 = not accepted/no pulse.
 * Range 100..100000 us follows native configuration. Results must be retired
 * before another attempt; the driver also enforces >=70 ms between triggers. */
uint32_t UltrasonicReader_Begin(uint32_t echo_timeout_us);
void UltrasonicReader_TimerIrq(void);
void UltrasonicReader_EchoIrq(void);
/* Read/retire an exact completed observation. Copy never refreshes capture time
 * or restarts acquisition. Publish failures must not retire the observation. */
uint8_t UltrasonicReader_Copy(UltrasonicObservation *output);
uint8_t UltrasonicReader_Retire(uint32_t attempt_sequence);
/* Foreground reservation for a multi-attempt owner at a stable non-NULL address.
 * Raw APIs use the unreserved (NULL) owner and cannot steal reserved attempts.
 * Claim/release require no pending or held observation. IRQs keep progressing.
 * Never reinitialize the driver or move/reset an owner while it holds a claim. */
uint8_t UltrasonicReader_Claim(const void *owner);
uint8_t UltrasonicReader_Release(const void *owner);
uint32_t UltrasonicReader_BeginOwned(const void *owner, uint32_t echo_timeout_us);
uint8_t UltrasonicReader_CopyOwned(const void *owner, UltrasonicObservation *output);
uint8_t UltrasonicReader_RetireOwned(const void *owner, uint32_t attempt_sequence);
/* Cancel only a pending reserved attempt (or confirm it is idle). Never discard
 * a held observation or manufacture UNAVAILABLE; consume a real completion
 * first. The owner retains its reservation until explicit Release. */
uint8_t UltrasonicReader_CancelOwned(const void *owner);
#endif
