#include <assert.h>
#include "door_control.h"

static void test_close_pauses_without_losing_target_and_resumes(void)
{
    DoorControl door;
    DoorControlSnapshot state;
    DoorControl_Init(&door);
    assert(DoorControl_SetTarget(&door, MCU_DIRECTION_CLOSE));
    state = DoorControl_Read(&door, 1U);
    assert(state.target_valid && state.action_active);
    assert(state.target == MCU_DIRECTION_CLOSE);
    assert(state.pinch_paused && !state.pb6 && !state.pb7);
    state = DoorControl_Read(&door, 0U);
    assert(!state.pinch_paused && !state.pb6 && state.pb7);
}

int main(void)
{
    test_close_pauses_without_losing_target_and_resumes();
    {
        DoorControl door;
        DoorControlSnapshot state;
        DoorControl_Init(&door);
        state = DoorControl_Read(&door, 1U);
        assert(!state.target_valid && !state.pb6 && !state.pb7);
        assert(DoorControl_SetTarget(&door, MCU_DIRECTION_CLOSE));
        DoorControl_Stop(&door);
        state = DoorControl_Read(&door, 0U);
        assert(state.target_valid && state.target == MCU_DIRECTION_CLOSE);
        assert(!state.action_active && !state.pb6 && !state.pb7);
        assert(DoorControl_SetTarget(&door, MCU_DIRECTION_OPEN));
        state = DoorControl_Read(&door, 1U);
        assert(state.pb6 && !state.pb7 && !state.pinch_paused);
    }
    return 0;
}
