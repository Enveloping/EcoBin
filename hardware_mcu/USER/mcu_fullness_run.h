#ifndef ECOBIN_MCU_FULLNESS_RUN_H
#define ECOBIN_MCU_FULLNESS_RUN_H
#include "mcu_environment_monitor.h"

#define MCU_FULLNESS_RUN_COMPLETE 1u
#define MCU_FULLNESS_RUN_INTERRUPTED 2u
#define MCU_FULLNESS_STOP_CALLER 1u
#define MCU_FULLNESS_STOP_CONFIGURATION 2u

typedef struct {
    uint64_t config_version, started_ms, completed_ms, last_captured_ms;
    uint8_t content_sha256[32], mcu_sha256[32];
    uint32_t sequence, distance_mm;
    uint8_t status, stop_reason, port_no, sensor_kind, sensor_value, basis;
    uint8_t distance_present, requested_count, completed_count, valid_count;
} McuFullnessResult;

typedef struct {
    McuFullnessResult result;
    McuConfigFullnessPolicy policy;
    McuDeviceFacts *facts;
    const McuConfiguration *configuration;
    uint16_t distances[9];
    uint32_t attempt_sequence;
    uint8_t present;
} McuFullnessRun;

/* One foreground owner at a stable address, initialized once at MCU boot.
 * Begin reserves the sole board-port-1 ultrasonic source and freezes verified
 * applied settings. Facts/config pointers must outlive the run. No allocation,
 * blocking waits, command acceptance, business authorization or bag changes.
 * Poll starts at most one actual attempt, never fills missed sampling slots.
 * Keep timer/echo IRQs running independently of this foreground owner.
 * COMPLETE is local sensor-group completion, NOT UART/SQLite custody or release
 * of business. The integrating owner must retain/bind this result before Retire.
 */
void McuFullnessRun_Init(McuFullnessRun *run);
uint32_t McuFullnessRun_Begin(McuFullnessRun *run, McuDeviceFacts *facts, const McuConfiguration *configuration);
/* Returns 1 when a terminal result is held; repeated polls/copies never refresh. */
uint8_t McuFullnessRun_Poll(McuFullnessRun *run);
/* Latch stop for an unfinished group, drain real completed observations and
 * cancel only an outstanding attempt. No fake echo failure or CLEAR decision.
 * Repeated Interrupt/Poll can finish draining a temporarily unpublishable
 * observation. Already frozen terminal results are never reclassified. */
uint8_t McuFullnessRun_Interrupt(McuFullnessRun *run);
uint8_t McuFullnessRun_Copy(const McuFullnessRun *run, McuFullnessResult *output);
uint8_t McuFullnessRun_Retire(McuFullnessRun *run, uint32_t sequence);
#endif
