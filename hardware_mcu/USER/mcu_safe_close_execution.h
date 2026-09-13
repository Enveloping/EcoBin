#ifndef ECOBIN_MCU_SAFE_CLOSE_EXECUTION_H
#define ECOBIN_MCU_SAFE_CLOSE_EXECUTION_H
#include "mcu_work_preparation.h"

/* Explicit candidate standalone recovery CLOSE. No main activation and no
 * cleanup/permission for the old Pi work. Requires the application's SAFE_CLOSE
 * guard, one result credit and a new session command. Only this endpoint's
 * SINGLE_DELIVERY_DOOR is supported; not clean-door or running-work preemption.
 * Acceptance is cached before output; exact actuator custody retires only this
 * attempt. A failed/expired output is not turned into successful door closure. */
typedef struct {
    McuWorkPreparation *preparation;
    McuActuatorEventReservation record;
    uint64_t rejected_at_ms;
    uint32_t token;
    uint8_t command_uid[16];
    uint8_t port_no;
    uint8_t active;
    uint8_t published;
} McuSafeCloseExecution;

uint8_t McuSafeCloseExecution_Attach(McuSafeCloseExecution *owner,
    McuWorkPreparation *preparation, McuControlEndpoint *endpoint);
#endif
