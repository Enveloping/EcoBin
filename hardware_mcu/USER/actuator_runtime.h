#ifndef ECOBIN_ACTUATOR_RUNTIME_H
#define ECOBIN_ACTUATOR_RUNTIME_H
#include "door_control.h"

#define ACTUATOR_PB6 1U
#define ACTUATOR_PB7 2U
#define ACTUATOR_PB8 4U
typedef struct {
    uint32_t (*enter_critical)(void);
    void (*leave_critical)(uint32_t previous_mask);
    uint8_t (*read_pinch)(void);
    void (*write_outputs)(uint8_t mask);
} ActuatorHardware;
typedef struct {
    /* Last applied GPIO command, not a physical door-position measurement. */
    DoorControlSnapshot door;
    /* Input used in that SAME control update, not a later GPIO read. */
    uint8_t pinch_input_active;
    uint8_t lock_powered;
    uint8_t update_latched;
    uint64_t control_uptime_ms;
    uint64_t captured_uptime_ms;
} ActuatorSnapshot;

/* Native timed cycle evidence, retained until the foreground owner releases it.
 * Open/close are logical command dispatch, NEVER a measured physical position.
 * Timer owns both 100ms all-off intervals and the automatic close deadline.
 * Beginning a cycle is NOT authorization: the application first retains the
 * accepted original START. Optional diagnostics do not gate this timer owner.
 */
typedef struct {
    uint32_t token;
    uint64_t requested_at_ms;
    uint64_t execution_deadline_ms;
    uint64_t opened_at_ms;
    uint64_t closed_at_ms;
    uint64_t terminal_at_ms;
    uint8_t present;
    uint8_t opened;
    uint8_t closed;
    uint8_t expired;
    uint8_t interrupted;
} ActuatorDeliveryCycle;

/* One native solenoid pulse. Both applied edges survive foreground absence.
 * This is GPIO command evidence, not coil health or physical door position. */
typedef struct {
    uint32_t token;
    uint64_t powered_at_ms;
    uint64_t off_deadline_ms;
    uint64_t off_at_ms;
    uint8_t present;
    uint8_t completed;
    uint8_t interrupted;
} ActuatorCleanPulse;

/* Standalone recovery CLOSE, independent of any old delivery cycle. It never
 * touches the clean lock. Retain until exact event custody; no implicit replay.
 * completed means the attempt is terminal, not physically closed. */
typedef struct {
    uint64_t deadline_ms;
    uint64_t due_ms;
    uint64_t terminal_at_ms;
    uint32_t token;
    uint8_t present;
    uint8_t completed;
    uint8_t coalesced;
    uint8_t rejected;
} ActuatorRecoveryClose;

/* Init only at boot, before enabling the timer. All hooks must be provided. */
void ActuatorRuntime_Init(const ActuatorHardware *hardware);
uint8_t ActuatorRuntime_SetDoorTarget(uint8_t target);
uint8_t ActuatorRuntime_Unlock(uint32_t duration_ms);
/* Explicit native API, not used by the legacy main loop. First accepted native cycle/pulse
 * disables legacy SetDoorTarget/Unlock until MCU reset, even after release.
 * While custody is held another cycle cannot override its deadlines.
 * Returns a non-wrapping boot-local token, or zero without changing outputs. */
uint32_t ActuatorRuntime_BeginDeliveryCycle(uint64_t execute_before_ms, uint32_t auto_close_ms);
ActuatorDeliveryCycle ActuatorRuntime_DeliveryCycle(void);
/* Current local HMI "finished placing items": shorten only this opened cycle's
 * countdown. Still applies the timer-owned 100 ms all-off reversal interval.
 * Duplicate requests cannot restart/extend the interval or act on another run. */
uint8_t ActuatorRuntime_RequestDeliveryClose(uint32_t token);
/* Only terminal exact token. Does not stop CLOSE or clear update latch. */
uint8_t ActuatorRuntime_ReleaseDeliveryCycle(uint32_t token);
/* Caller first validates the accepted original work/local button context.
 * Immediate dispatch before execute_before_ms; requires logical CLOSE (PB5 may
 * pause it). Timer independently de-energizes. No legacy override after native
 * entry. A retained pulse blocks another pulse/delivery until exact release. */
uint32_t ActuatorRuntime_BeginCleanPulse(uint64_t execute_before_ms, uint32_t duration_ms);
ActuatorCleanPulse ActuatorRuntime_CleanPulse(void);
/* Stop only this retained pulse, preserving actual OFF time if already complete.
 * No door command, update latch, custody release or inference about door position. */
uint8_t ActuatorRuntime_AbortCleanPulse(uint32_t token);
uint8_t ActuatorRuntime_ReleaseCleanPulse(uint32_t token);
/* Fresh/idle recovery only: retained delivery/clean runs are not preempted.
 * Caller owns authorization and reserves result custody before acceptance.
 * New CLOSE waits 100 ms all-off; an existing active CLOSE is not interrupted. */
uint32_t ActuatorRuntime_BeginRecoveryClose(uint64_t execute_before_ms);
ActuatorRecoveryClose ActuatorRuntime_RecoveryClose(void);
uint8_t ActuatorRuntime_ReleaseRecoveryClose(uint32_t token);
void ActuatorRuntime_StopForUpdate(void);
/* Only the timer ISR calls Tick; no UART, delay or allocation in its hooks. */
void ActuatorRuntime_Tick(void);
ActuatorSnapshot ActuatorRuntime_Snapshot(void);
#endif
