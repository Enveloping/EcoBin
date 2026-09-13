#ifndef ECOBIN_DOOR_CONTROL_H
#define ECOBIN_DOOR_CONTROL_H

#include <stdint.h>
#include "mcu_runtime_logic.h"

/* One-byte publication permits an ISR to read a coherent target/action pair. */
typedef struct { uint8_t command; } DoorControl;
typedef struct {
    uint8_t target_valid;
    uint8_t action_active;
    uint8_t target;
    uint8_t pb6;
    uint8_t pb7;
    uint8_t pinch_paused;
} DoorControlSnapshot;

void DoorControl_Init(volatile DoorControl *door);
uint8_t DoorControl_SetTarget(volatile DoorControl *door, uint8_t target);
void DoorControl_Stop(volatile DoorControl *door);
DoorControlSnapshot DoorControl_Read(
    const volatile DoorControl *door, uint8_t pb5_active);

#endif
