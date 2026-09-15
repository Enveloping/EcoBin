#ifndef ECOBIN_MCU_DELIVERY_EXECUTION_H
#define ECOBIN_MCU_DELIVERY_EXECUTION_H
#include "mcu_opening_gate.h"

/* START is the sole Pi authorization. The MCU owns each local cycle. Only the
 * complete final result requires durable ACK; diagnostics never gate motion.
 * Door states are control states, not physical position assertions. */
typedef struct {
    McuWorkPreparation *preparation;
    uint8_t start_uid[16];
    uint64_t closed_at_ms, selection_deadline_ms, selected_at_ms, last_now_ms;
    McuResultMeasurement postclose;
    McuProcessMeasurementMeta postclose_meta;
    uint32_t cycle_token, close_travel_wait_ms;
    int32_t round_before_grams;
    uint16_t round_index;
    uint8_t active, opened, close_requested;
    uint8_t postclose_state; /* 0 absent, 1 travel wait, 2 measuring, 3 retained. */
    uint8_t selection, negative_weight_anomaly, interrupted;
} McuDeliveryExecution;

uint8_t McuDeliveryExecution_Attach(McuDeliveryExecution *owner,
    McuWorkPreparation *preparation, McuControlEndpoint *endpoint);
/* Foreground HMI: displayed measurement UUID rejects stale round input.
 * Only CONTINUE/END are buttons; expiry uses the MCU clock, without Pi ACK. */
uint8_t McuDeliveryExecution_Select(McuDeliveryExecution *owner, McuControlEndpoint *endpoint,
    const uint8_t *postclose_measurement_uid, uint8_t selection, uint64_t now_ms);
/* The HMI can locally show its END/CONTINUE page as soon as the operator asks
 * to close. Retain that one current-round intent until the post-close weight
 * exists, then bind it to that measurement. It is never accepted before a
 * successful close request and cannot cross a round or START boundary. */
uint8_t McuDeliveryExecution_RequestSelection(McuDeliveryExecution *owner,
    McuControlEndpoint *endpoint, uint8_t selection, uint64_t now_ms);
/* Current local "placing finished" button, not final END and not a Pi command. */
uint8_t McuDeliveryExecution_CloseCurrent(McuDeliveryExecution *owner, McuControlEndpoint *endpoint, uint64_t now_ms);
#endif
