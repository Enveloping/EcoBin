#ifndef __ECOBIN_MCU_UPDATE_EXECUTION_H
#define __ECOBIN_MCU_UPDATE_EXECUTION_H

/*
 * Pure transition used by the F2/02 firmware-update execution command.
 *
 * Business admission is deliberately absent here.  The Orange Pi has already
 * decided that an update may start.  The MCU only latches update mode, stops
 * all local flows and turns every controlled output off.
 */

#define MCU_UPDATE_FLAG_WORK_IDLE       0x01U
#define MCU_UPDATE_FLAG_CLEANING_IDLE   0x02U
#define MCU_UPDATE_FLAG_DRIVE_OFF       0x04U
#define MCU_UPDATE_FLAG_LOCK_OFF        0x08U
#define MCU_UPDATE_FLAG_LATCHED         0x10U
#define MCU_UPDATE_ALL_FLAGS            0x1FU

#define MCU_UPDATE_EXECUTION_OK          0x00U
#define MCU_UPDATE_EXECUTION_INTERNAL    0x03U

typedef struct
{
    unsigned char delivery_active;
    unsigned char cleaning_state;
    unsigned char command_direction;
    unsigned char weigh_state;
    unsigned char relay1;
    unsigned char relay2;
    unsigned char lock_output;
    unsigned char update_latched;
} McuUpdateExecutionState;

void McuUpdateExecution_Apply(McuUpdateExecutionState *state);
unsigned char McuUpdateExecution_Flags(
    const McuUpdateExecutionState *state);
unsigned char McuUpdateExecution_Status(
    const McuUpdateExecutionState *state);

#endif /* __ECOBIN_MCU_UPDATE_EXECUTION_H */
