#ifndef ECOBIN_MCU_OPENING_GATE_H
#define ECOBIN_MCU_OPENING_GATE_H
#include "mcu_work_preparation.h"

typedef struct {
    uint64_t execution_deadline_ms;
    uint64_t operation_deadline_ms;
    uint32_t delivery_auto_close_ms;
    uint32_t unlock_pulse_ms;
} McuOpeningLimits;

/* Candidate READ-ONLY first-opening preflight, not an execution owner.
 * NONE means only these prerequisites passed; it does NOT accept a command,
 * emit an event, change work/sequence, energize outputs or prove photographs.
 * Caller must first resolve cached/old command decisions with McuSession; a
 * non-new command here returns IDEMPOTENCY_CONFLICT, NOT a replacement wire
 * decision. After preflight, the eventual owner must atomically reserve event
 * custody, cache acceptance, and execute once, with direction dead time and
 * auto-close scheduling. Those paths are not implemented/attached here.
 *
 * received_ms is the ORIGINAL complete-frame reception time, never refreshed
 * by retries/rechecks. now_ms uses the same 64-bit local monotonic clock.
 * Caller owns immutable reception context and serializes with Feed/Poll;
 * the mandatory application guard must remain non-mutating/non-reentrant.
 * Output is external/non-overlapping and unchanged on failure. Its deadlines
 * are limits for the eventual executor, not durable/reusable permission.
 * Only first delivery open and first clean unlock belong to this gate; clean
 * reopening/recovery require their own real request/sequence context later.
 */
uint16_t McuOpeningGate_Evaluate(const McuWorkPreparation *owner,
    const McuControlEndpoint *endpoint, uint8_t message, const uint8_t *payload,
    size_t length, uint64_t received_ms, uint64_t now_ms, McuOpeningLimits *output);
/* Shared read-only original-work configuration proof, not an action grant.
 * Non-null attached preparation/endpoint required. */
uint8_t McuOpeningGate_ConfigurationMatches(const McuWorkPreparation *owner, const McuControlEndpoint *endpoint);
/* Exact initial-data custody only, including unavailable measurements. Non-null
 * attached owners required; clean must match the caller's retained work type.
 * Does not imply usable weight, motion permission, safe door or work release. */
uint8_t McuOpeningGate_InitialSaved(const McuWorkPreparation *owner, const McuControlEndpoint *endpoint, uint8_t clean);
#endif
