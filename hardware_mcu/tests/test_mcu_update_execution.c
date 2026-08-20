#include <assert.h>

#include "mcu_update_execution.h"

static void test_busy_state_is_stopped_instead_of_rejected(void)
{
    McuUpdateExecutionState state = {
        1U, 2U, 2U, 2U, 1U, 1U, 1U, 0U
    };

    McuUpdateExecution_Apply(&state);

    assert(state.delivery_active == 0U);
    assert(state.cleaning_state == 0U);
    assert(state.command_direction == 0U);
    assert(state.weigh_state == 0U);
    assert(state.relay1 == 0U);
    assert(state.relay2 == 0U);
    assert(state.lock_output == 0U);
    assert(state.update_latched == 1U);
    assert(McuUpdateExecution_Flags(&state) == MCU_UPDATE_ALL_FLAGS);
    assert(McuUpdateExecution_Status(&state) == MCU_UPDATE_EXECUTION_OK);
}

static void test_execution_is_idempotent(void)
{
    McuUpdateExecutionState state = {
        0U, 0U, 0U, 0U, 0U, 0U, 0U, 1U
    };

    McuUpdateExecution_Apply(&state);
    McuUpdateExecution_Apply(&state);

    assert(McuUpdateExecution_Status(&state) == MCU_UPDATE_EXECUTION_OK);
}

static void test_incomplete_output_shutdown_is_internal_error(void)
{
    McuUpdateExecutionState state = {
        0U, 0U, 0U, 0U, 0U, 1U, 0U, 1U
    };

    assert(McuUpdateExecution_Flags(&state) == 0x1BU);
    assert(McuUpdateExecution_Status(&state) ==
           MCU_UPDATE_EXECUTION_INTERNAL);
}

int main(void)
{
    test_busy_state_is_stopped_instead_of_rejected();
    test_execution_is_idempotent();
    test_incomplete_output_shutdown_is_internal_error();
    return 0;
}
