#ifndef ECOBIN_MCU_CONFIG_COLLECTION_H
#define ECOBIN_MCU_CONFIG_COLLECTION_H
#include "../../contracts/uart/generated/c/ecobin_uart_protocol.h"
#include "weight_measurement.h"

/* Internal collection outcomes, NOT UART enums or configuration application.
 * COMPLETE means the full declared byte set has been verified, not APPLIED.
 */
#define MCU_CONFIG_COLLECTION_INVALID 0u
#define MCU_CONFIG_COLLECTION_STAGED 1u
#define MCU_CONFIG_COLLECTION_COMPLETE 2u
#define MCU_CONFIG_COLLECTION_DUPLICATE 3u
#define MCU_CONFIG_COLLECTION_WRONG_BOOT 4u
#define MCU_CONFIG_COLLECTION_CONFLICT 5u
#define MCU_CONFIG_COLLECTION_INCOMPLETE 6u
#define MCU_CONFIG_COLLECTION_DIGEST_MISMATCH 7u
#define MCU_CONFIG_COLLECTION_PORT_UNSUPPORTED 8u

typedef struct {
    uint64_t boot_id;
    uint8_t application_uid[16], expected_digest[32];
    uint8_t preimage[ECOBIN_UART_CONFIG_PREIMAGE_MAX_LENGTH];
    uint16_t received_parts;
    uint8_t port_capacity, expected_ports, part_count, complete;
} McuConfigCollection;

typedef struct {
    uint64_t config_version;
    uint32_t calibration_version, poll_interval_ms, response_timeout_ms;
    WeightMeasurementConfig measurement;
    uint8_t port_no, enabled;
} McuConfigWeightPolicy;

/* Sensor-group acquisition policy, NOT the edge-owned current-bag decision. */
typedef struct {
    uint64_t config_version;
    uint32_t echo_timeout_us, settle_wait_ms, distance_threshold_mm;
    uint8_t port_no, enabled, sensor_kind, sample_count, minimum_valid_count;
} McuConfigFullnessPolicy;

/* Single foreground-owned candidate storage, separate from applied config and
 * business/result slots. Init starts a new collection; the future owner must
 * explicitly discard/consume its old candidate before doing so. Pi reconnect
 * does not initialize it. No allocator, timer, GPIO, Flash or automatic apply.
 */
void McuConfigCollection_Init(McuConfigCollection *collection, uint64_t boot_id, uint8_t port_capacity);
/* Caller separately fences command sequence/acceptance with McuSession before
 * mutating this collector. Whole shape/digest and target boot are checked here
 * too. Does not set command high-water or emit acceptance on the caller's behalf.
 * Semantic duplicates ignore the command envelope but require identical set
 * identity/part count and data. Other transactions cannot overwrite this one.
 */
uint8_t McuConfigCollection_Offer(McuConfigCollection *collection, uint8_t message,
    const uint8_t *payload, size_t length);
/* Exports exact mcuPayloadSha256 preimage only after COMPLETE, not padded C
 * memory. Full settings still require native-policy validation and atomic
 * application by their owner. Buffer must be external/non-overlapping.
 */
size_t McuConfigCollection_CopyComplete(const McuConfigCollection *collection, uint8_t *output, size_t capacity);
/* Typed projection from a COMPLETE immutable candidate. No defaults or hidden
 * overrides: every effective parameter comes from the verified digest preimage.
 * Returns 0 without touching output for incomplete/unsupported requests. This
 * does not apply config, authorize work, poll hardware or calibrate the scale.
 * The future configuration owner must activate the full set and publish facts
 * before supplying this policy to the business/measurement owner.
 */
uint8_t McuConfigCollection_ReadWeightPolicy(const McuConfigCollection *collection,
    uint8_t port_no, McuConfigWeightPolicy *output);
uint8_t McuConfigCollection_ReadFullnessPolicy(const McuConfigCollection *collection,
    uint8_t port_no, McuConfigFullnessPolicy *output);
#endif
