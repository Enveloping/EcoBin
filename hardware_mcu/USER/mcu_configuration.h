#ifndef ECOBIN_MCU_CONFIGURATION_H
#define ECOBIN_MCU_CONFIGURATION_H
#include "mcu_config_collection.h"
#include "mcu_session.h"
#include "mcu_work_state.h"

/* Native candidate: single foreground owner, no ISR readers. Three bounded
 * banks keep non-mutating validation off the stack and preserve active settings
 * until a complete commit has been accepted by the actual command session.
 * No GPIO, Flash, UART, business admission, cloud events or automatic recovery.
 */
typedef struct {
    McuConfigCollection active, staging, trial;
} McuConfiguration;

/* MCU reset/new boot binding only, NEVER on Pi reconnect. Applied settings are
 * RAM-only. Actual consumers/main are not connected to this candidate yet.
 */
void McuConfiguration_Init(McuConfiguration *state, uint64_t boot_id, uint8_t port_count);
/* Full configuration payload (not frame). owner_error is the mandatory result
 * of the owner checking OTHER work/safety/update/configuration prerequisites;
 * NONE is not a default authorization. This module additionally checks actual
 * delivery/clean work and held results. Preconditions must be non-mutating.
 * Session acceptance is cached before banks change; duplicate/old/rejected
 * commands cannot execute again. 0 = malformed/no decision; otherwise decision
 * has actual McuSession outcome. No wire APPLIED event is emitted: the complete
 * owner must connect every consumer before claiming whole-device application.
 * A newly accepted BEGIN with a different application may replace only the
 * staged candidate, never active settings or business. Version rollback and
 * same-version/different-digest sets are refused; an application UID cannot be
 * repurposed to a different version. New work must also check IsStaging.
 */
uint8_t McuConfiguration_Receive(McuConfiguration *state, McuSession *session,
    const McuWorkState *work, uint16_t owner_error, uint8_t message,
    const uint8_t *payload, size_t length, McuSessionDecision *decision);
size_t McuConfiguration_CopyActive(const McuConfiguration *state, uint8_t *output, size_t capacity);
uint8_t McuConfiguration_ReadWeightPolicy(const McuConfiguration *state, uint8_t port_no, McuConfigWeightPolicy *output);
uint8_t McuConfiguration_ReadFullnessPolicy(const McuConfiguration *state, uint8_t port_no, McuConfigFullnessPolicy *output);
uint8_t McuConfiguration_IsStaging(const McuConfiguration *state);
#endif
