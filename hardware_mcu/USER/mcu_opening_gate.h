#ifndef ECOBIN_MCU_OPENING_GATE_H
#define ECOBIN_MCU_OPENING_GATE_H
#include "mcu_work_preparation.h"
typedef struct {
    uint64_t execution_deadline_ms, operation_deadline_ms;
    uint32_t delivery_auto_close_ms, unlock_pulse_ms;
} McuOpeningLimits;
/* The already accepted START supplies authorization and immutable deadlines.
 * No separate Pi grant or process SAVED is required. */
uint16_t McuOpeningGate_StartLimits(const McuWorkPreparation *owner,
    const McuControlEndpoint *endpoint, uint64_t now_ms, McuOpeningLimits *output);
uint8_t McuOpeningGate_ConfigurationMatches(const McuWorkPreparation *owner, const McuControlEndpoint *endpoint);
/* Historical candidate API explicitly unsupported in the simplified target. */
uint16_t McuOpeningGate_Evaluate(const McuWorkPreparation *owner,
    const McuControlEndpoint *endpoint, uint8_t message, const uint8_t *payload,
    size_t length, uint64_t received_ms, uint64_t now_ms, McuOpeningLimits *output);
#endif
