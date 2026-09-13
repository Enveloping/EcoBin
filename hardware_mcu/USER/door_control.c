#include "door_control.h"

#define TARGET_VALID 0x80U
#define ACTION_ACTIVE 0x40U
#define TARGET_MASK 0x03U

void DoorControl_Init(volatile DoorControl *door)
{
    door->command = 0U;
}

void DoorControl_Stop(volatile DoorControl *door)
{
    door->command = (uint8_t)(door->command & ~ACTION_ACTIVE);
}

uint8_t DoorControl_SetTarget(volatile DoorControl *door, uint8_t target)
{
    if(target != MCU_DIRECTION_CLOSE && target != MCU_DIRECTION_OPEN)
        return 0U;
    door->command = (uint8_t)(TARGET_VALID | ACTION_ACTIVE | target);
    return 1U;
}

DoorControlSnapshot DoorControl_Read(
    const volatile DoorControl *door, uint8_t pb5_active)
{
    uint8_t command = door->command;
    DoorControlSnapshot state;
    state.target_valid = (command & TARGET_VALID) != 0U;
    state.action_active = (command & ACTION_ACTIVE) != 0U;
    state.target = (uint8_t)(command & TARGET_MASK);
    state.pb6 = 0U;
    state.pb7 = 0U;
    state.pinch_paused = 0U;
    if(state.target_valid && state.action_active)
    {
        if(state.target == MCU_DIRECTION_OPEN)
            state.pb6 = 1U;
        else if(state.target == MCU_DIRECTION_CLOSE)
        {
            state.pinch_paused = pb5_active != 0U;
            state.pb7 = !state.pinch_paused;
        }
    }
    return state;
}
