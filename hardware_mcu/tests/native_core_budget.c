/* LINK-ONLY size probe. NOT a bootable firmware, never execute or flash it.
 * Retain the public candidate entry points and one explicit instance of each
 * foreground owner. Includes the native ultrasonic register adapter, but does
 * not model startup/main integration, physical I/O behavior or IRQ stack.
 */
#include "mcu_control_endpoint.h"
#include "mcu_configuration.h"
#include "mcu_result_builder.h"
#include "mcu_process_measurement.h"
#include "mcu_weight_run.h"
#include "mcu_work_preparation.h"
#include "mcu_opening_gate.h"
#include "mcu_delivery_execution.h"
#include "mcu_clean_execution.h"
#include "mcu_safe_close_execution.h"
#include "mcu_actuator_event_journal.h"
#include "actuator_runtime.h"
#include "runtime_clock.h"
#include "scale_reader.h"
#include "mcu_environment_monitor.h"
#include "smoke_monitor.h"
#include "ultrasonic_reader.h"
#include "ultrasonic_stm32.h"
#include "mcu_fullness_run.h"

/* Link-only ADC boundary, NOT a production driver or health simulation. */
unsigned char ADC1_TryRead(unsigned short *value) { *value = 0u; return 0u; }

typedef void (*Entry)(void);
#define ROOT(function) ((Entry)(function))
static Entry const entries[] = {
    ROOT(McuControlEndpoint_Init), ROOT(McuControlEndpoint_Feed),
    ROOT(McuControlEndpoint_AttachCommands), ROOT(McuControlEndpoint_ReserveEventSequence),
    ROOT(McuControlEndpoint_ReserveActuatorEvents), ROOT(McuControlEndpoint_CancelActuatorEvents),
    ROOT(McuControlEndpoint_PublishActuatorEvent), ROOT(McuControlEndpoint_CopyNextActuatorEvent),
    ROOT(McuControlEndpoint_ConfirmActuatorEventSaved),
    ROOT(McuWorkPreparation_Attach), ROOT(McuWorkPreparation_Poll), ROOT(McuWorkPreparation_CopyStart),
    ROOT(McuWorkPreparation_AttachFullness),
    ROOT(McuOpeningGate_Evaluate),
    ROOT(McuDeliveryExecution_Attach), ROOT(McuDeliveryExecution_Select), ROOT(McuWorkPreparation_AttachActions), ROOT(McuWorkPreparation_PollMeasurement),
    ROOT(McuCleanExecution_Attach), ROOT(McuCleanExecution_Request), ROOT(McuCleanExecution_Confirm),
    ROOT(McuConfiguration_Init), ROOT(McuConfiguration_Receive), ROOT(McuConfiguration_CopyActive),
    ROOT(McuConfiguration_ReadWeightPolicy), ROOT(McuConfiguration_IsStaging),
    ROOT(McuConfigCollection_Init), ROOT(McuConfigCollection_Offer), ROOT(McuConfigCollection_CopyComplete),
    ROOT(McuConfigCollection_ReadWeightPolicy),
    ROOT(McuSession_Init), ROOT(McuSession_Probe), ROOT(McuSession_Bind),
    ROOT(McuSession_ReceiveCommand), ROOT(McuSession_QueryCommand),
    ROOT(McuWorkState_Init), ROOT(McuWorkState_CanBegin), ROOT(McuWorkState_BeginAccepted), ROOT(McuWorkState_SetPhase),
    ROOT(McuWorkState_Complete), ROOT(McuWorkState_Saved), ROOT(McuWorkState_CopyHeld), ROOT(McuWorkState_Query),
    ROOT(McuResultSlot_Init), ROOT(McuResultSlot_Freeze), ROOT(McuResultSlot_CopyHeld),
    ROOT(McuResultSlot_Query), ROOT(McuResultSlot_Saved),
    ROOT(McuResultBuilder_Complete), ROOT(McuResultMeasurement_FromAvailable),
    ROOT(McuProcessMeasurement_BuildWorkEvent), ROOT(McuProcessMeasurement_BuildBaselineEvent),
    ROOT(McuProcessEventSlot_Init), ROOT(McuProcessEventSlot_Freeze), ROOT(McuProcessEventSlot_CopyHeld),
    ROOT(McuProcessEventSlot_Query), ROOT(McuProcessEventSlot_Saved),
    ROOT(McuActuatorEventJournal_Init), ROOT(McuActuatorEventJournal_Reserve),
    ROOT(McuActuatorEventJournal_Cancel), ROOT(McuActuatorEventJournal_Freeze),
    ROOT(McuActuatorEventJournal_CopyNextHeld), ROOT(McuActuatorEventJournal_ConfirmSaved),
    ROOT(McuActuatorEventJournal_PendingCount), ROOT(McuActuatorEventJournal_PublishNext),
    ROOT(McuActuatorEventJournal_Query), ROOT(McuActuatorEventJournal_Saved),
    ROOT(McuDeviceFacts_Init), ROOT(McuDeviceFacts_PublishConfiguration), ROOT(McuDeviceFacts_PublishMeasurement),
    ROOT(McuDeviceFacts_ObserveScale), ROOT(McuDeviceFacts_ScaleTimeout), ROOT(McuDeviceFacts_Capture),
    ROOT(McuDeviceFacts_PublishSmoke), ROOT(McuDeviceFacts_PublishFullness),
    ROOT(McuEnvironmentMonitor_PollSmoke), ROOT(SmokeMonitor_Init), ROOT(SmokeMonitor_UpdateSample),
    ROOT(McuEnvironmentMonitor_StartUltrasonic), ROOT(McuEnvironmentMonitor_PollUltrasonic),
    ROOT(UltrasonicStm32_Init), ROOT(TIM4_IRQHandler), ROOT(EXTI15_10_IRQHandler),
    ROOT(UltrasonicReader_Init), ROOT(UltrasonicReader_Begin), ROOT(UltrasonicReader_Copy), ROOT(UltrasonicReader_Retire),
    ROOT(McuFullnessRun_Init), ROOT(McuFullnessRun_Begin), ROOT(McuFullnessRun_Poll),
    ROOT(McuFullnessRun_Copy), ROOT(McuFullnessRun_Interrupt), ROOT(McuFullnessRun_Retire),
    ROOT(McuWeightRun_Init), ROOT(McuWeightRun_Begin), ROOT(McuWeightRun_StartOwnedAttempt),
    ROOT(McuWeightRun_FinishOwnedAttempt), ROOT(McuWeightRun_Poll), ROOT(McuWeightRun_Copy), ROOT(McuWeightRun_Retire),
    ROOT(McuWeightRun_Interrupt), ROOT(WeightMeasurement_Interrupt),
    ROOT(WeightMeasurement_ConfigValid), ROOT(WeightMeasurement_Begin), ROOT(WeightMeasurement_Observe), ROOT(WeightMeasurement_Poll),
    ROOT(ScaleReader_Crc16), ROOT(ScaleReader_Decode),
    ROOT(ActuatorRuntime_Init), ROOT(ActuatorRuntime_SetDoorTarget), ROOT(ActuatorRuntime_Unlock),
    ROOT(ActuatorRuntime_StopForUpdate), ROOT(ActuatorRuntime_Tick), ROOT(ActuatorRuntime_Snapshot),
    ROOT(ActuatorRuntime_BeginDeliveryCycle), ROOT(ActuatorRuntime_DeliveryCycle), ROOT(ActuatorRuntime_ReleaseDeliveryCycle),
    ROOT(ActuatorRuntime_BeginCleanPulse), ROOT(ActuatorRuntime_CleanPulse), ROOT(ActuatorRuntime_ReleaseCleanPulse),
    ROOT(McuSafeCloseExecution_Attach), ROOT(McuWorkPreparation_AttachRecovery),
    ROOT(ActuatorRuntime_BeginRecoveryClose), ROOT(ActuatorRuntime_RecoveryClose), ROOT(ActuatorRuntime_ReleaseRecoveryClose),
    ROOT(RuntimeClock_Init), ROOT(RuntimeClock_Advance), ROOT(RuntimeClock_Now),
    ROOT(RuntimeClock_Now64Locked), ROOT(RuntimeClock_PeriodDue)
};
static McuControlEndpoint endpoint;
static McuWorkPreparation preparation;
static McuDeliveryExecution delivery_execution;
static McuCleanExecution clean_execution;
static McuSafeCloseExecution safe_close_execution;
static McuResultMeasurement result_measurements[2];
static McuResultSummary result_summary;
static uint8_t result_scratch[ECOBIN_UART_WORK_RESULT_PAYLOAD_MAX_LENGTH];
/* The endpoint now contains the one shared actuator journal; no second owner. */
static const void *const owners[] = {&endpoint, &preparation, &delivery_execution, &clean_execution, &safe_close_execution,
    result_measurements, &result_summary, result_scratch};
static Entry volatile retained_function;
static const void *volatile retained_owner;

void NativeBudget_Entry(void) {
    size_t i;
    /* Keep relocation references without calling functions through incompatible
     * signatures. The image is never executed. */
    for (i = 0u; i < sizeof(entries) / sizeof(entries[0]); ++i) retained_function = entries[i];
    for (i = 0u; i < sizeof(owners) / sizeof(owners[0]); ++i) retained_owner = owners[i];
    for (;;) {}
}
