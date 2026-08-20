#include "mcu_update_execution.h"

void McuUpdateExecution_Apply(McuUpdateExecutionState *state)
{
    /* Latch first: subsequent local input must not restart an actuator. */
    state->update_latched = 1U;
    state->delivery_active = 0U;
    state->cleaning_state = 0U;
    state->command_direction = 0U;
    state->weigh_state = 0U;
    state->relay1 = 0U;
    state->relay2 = 0U;
    state->lock_output = 0U;
}

unsigned char McuUpdateExecution_Flags(
    const McuUpdateExecutionState *state)
{
    unsigned char flags = 0U;

    if(state->delivery_active == 0U && state->weigh_state == 0U)
        flags |= MCU_UPDATE_FLAG_WORK_IDLE;
    if(state->cleaning_state == 0U)
        flags |= MCU_UPDATE_FLAG_CLEANING_IDLE;
    if(state->command_direction == 0U &&
       state->relay1 == 0U && state->relay2 == 0U)
        flags |= MCU_UPDATE_FLAG_DRIVE_OFF;
    if(state->lock_output == 0U)
        flags |= MCU_UPDATE_FLAG_LOCK_OFF;
    if(state->update_latched != 0U)
        flags |= MCU_UPDATE_FLAG_LATCHED;

    return flags;
}

unsigned char McuUpdateExecution_Status(
    const McuUpdateExecutionState *state)
{
    return McuUpdateExecution_Flags(state) == MCU_UPDATE_ALL_FLAGS
        ? MCU_UPDATE_EXECUTION_OK
        : MCU_UPDATE_EXECUTION_INTERNAL;
}
