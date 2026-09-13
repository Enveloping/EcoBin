#ifndef ECOBIN_MCU_DELIVERY_EXECUTION_H
#define ECOBIN_MCU_DELIVERY_EXECUTION_H
#include "mcu_opening_gate.h"

/* Delivery cycles + timer automatic close, explicit candidate attachment.
 * Original full grant retained; first-round edges belong to that SAME command.
 * Later local rounds separately name the saved choice that caused the cycle.
 * No fabricated command UUID for a local opening or any automatic close.
 * Single foreground publisher; timer retains both dispatch timestamps so a
 * stalled foreground cannot overwrite OPEN with CLOSE. After configured CLOSE
 * travel wait, retain an independent measurement under the original START.
 * Exact post-weight SAVED opens the original configured selection window.
 * A first scoped button or MCU expiry freezes one choice; exact choice SAVED
 * permits END/expiry final assembly. An unavailable/interrupted post measurement instead
 * produces an explicit FAILED result after its own custody confirmation.
 * An unavailable/interrupted first pre-open measurement similarly yields FAILED after
 * custody: zero door rounds, final NOT_TAKEN, no door action or safety claim.
 * After CLOSE, foreground detects update stop or missing logical close context;
 * PB5 pause alone is not failure. One work-long third reservation holds the
 * independent interruption record without replacing actual OPEN/CLOSE.
 * Pending acquisition is interrupted with its original identity; completed
 * measurement/choice facts are preserved. Exact current output, interruption,
 * measurement and any existing choice custody precede FAILED assembly.
 * Already frozen final results win. Data completion is not safety recovery.
 * Before CLOSE, a rejected/expired/interrupted cycle retains explicit abort
 * evidence in the reserved closing slot. Both output and abort must be SAVED
 * before FAILED assembly. No close success/measurement is fabricated.
 * Saved CONTINUE may start one local cycle after independent local checks.
 * The window limits button acceptance, not later custody delivery. Local events name their saved cause,
 * not a new Pi command. The first grant remains the session authorization root.
 * Final result custody does not release Pi business occupancy or create money.
 * No main/HMI activation; actual scale ingress must prove response ownership.
 */
typedef struct {
    McuWorkPreparation *preparation;
    McuActuatorEventReservation open_record;
    McuActuatorEventReservation close_record;
    McuActuatorEventReservation interruption_record;
    uint8_t grant[ECOBIN_UART_AUTHORIZE_DELIVERY_FIRST_OPEN_PAYLOAD_MAX_LENGTH];
    uint64_t received_at_ms;
    uint64_t rejected_at_ms;
    uint64_t closed_at_ms;
    uint64_t aborted_at_ms;
    uint64_t interrupted_at_ms;
    uint64_t selection_deadline_ms;
    uint64_t selected_at_ms;
    uint64_t selection_last_now_ms;
    McuResultMeasurement postclose;
    McuProcessMeasurementMeta postclose_meta;
    uint32_t cycle_token;
    uint32_t close_travel_wait_ms;
    uint32_t selection_event_sequence;
    uint32_t local_cause_sequence;
    int32_t round_before_grams;
    uint16_t round_index;
    uint8_t active;
    uint8_t open_published;
    uint8_t close_published;
    uint8_t postclose_state; /* 0 absent, 1 travel wait, 2 measuring, 3 retained. */
    uint8_t selection; /* 0 undecided; otherwise the immutable Registry choice. */
    uint8_t negative_weight_anomaly;
    uint8_t abort_reason;
    uint8_t abort_opened;
    uint8_t interruption_reason;
    uint8_t interrupted_phase;
} McuDeliveryExecution;

uint8_t McuDeliveryExecution_Attach(McuDeliveryExecution *owner,
    McuWorkPreparation *preparation, McuControlEndpoint *endpoint);
/* Foreground HMI intent boundary: pass the measurement identity belonging to
 * the DISPLAYED round, never replace a delayed button's identity with a newer
 * round. Only CONTINUE/END are inputs; expiry belongs to the MCU clock.
 * Returns one only for the first accepted intent. Does not authorize motion.
 * Poll must first open the configured window after exact post-weight SAVED.
 * No UART/HMI driver is opened here; main must serialize Poll/Feed/button input.
 */
uint8_t McuDeliveryExecution_Select(McuDeliveryExecution *owner, McuControlEndpoint *endpoint,
    const uint8_t *postclose_measurement_uid, uint8_t selection, uint64_t now_ms);
#endif
