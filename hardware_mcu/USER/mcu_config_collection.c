#include "mcu_config_collection.h"
#include <string.h>

typedef char config_collection_ram_budget[(sizeof(McuConfigCollection) <= 512u) ? 1 : -1];
#define FIELD(name) ECOBIN_UART_CONFIG_BEGIN_##name##_OFFSET

static size_t preimage_length(const McuConfigCollection *collection) {
    return ECOBIN_UART_CONFIG_PREIMAGE_PORTS_OFFSET
        + collection->expected_ports * ECOBIN_UART_CONFIG_PORT_SEMANTIC_LENGTH;
}

void McuConfigCollection_Init(McuConfigCollection *collection, uint64_t boot_id, uint8_t port_capacity) {
    memset(collection, 0, sizeof(*collection));
    if (boot_id <= UINT64_C(9007199254740991) && port_capacity >= 1u && port_capacity <= ECOBIN_UART_CONFIG_MAX_PORTS) {
        collection->boot_id = boot_id;
        collection->port_capacity = port_capacity;
    }
}

static uint8_t same_set(const McuConfigCollection *collection, const uint8_t *payload) {
    return (uint8_t)(payload[FIELD(PART_COUNT)] == collection->part_count
        && memcmp(payload + FIELD(APPLICATION_UID), collection->application_uid, 16u) == 0
        && memcmp(payload + FIELD(CONFIG_VERSION), collection->preimage + ECOBIN_UART_CONFIG_PREIMAGE_VERSION_OFFSET, 8u) == 0
        && memcmp(payload + FIELD(CONTENT_SHA256), collection->preimage + ECOBIN_UART_CONFIG_PREIMAGE_CONTENT_SHA256_OFFSET, 32u) == 0
        && memcmp(payload + FIELD(MCU_PAYLOAD_SHA256), collection->expected_digest, 32u) == 0);
}

uint8_t McuConfigCollection_Offer(McuConfigCollection *collection, uint8_t message,
    const uint8_t *payload, size_t length) {
    static const uint8_t domain[] = ECOBIN_UART_CONFIG_DOMAIN_BYTES;
    uint8_t part, port;
    uint16_t bit, required;
    size_t start, count;
    uint8_t digest[32];
    if (collection == NULL || payload == NULL || length > ECOBIN_UART_MAX_PAYLOAD_LENGTH
        || (message != ECOBIN_UART_MESSAGE_CONFIG_BEGIN && message != ECOBIN_UART_MESSAGE_CONFIG_DEVICE_BLOCK
            && message != ECOBIN_UART_MESSAGE_CONFIG_PORT_BLOCK && message != ECOBIN_UART_MESSAGE_CONFIG_COMMIT)
        || ecobin_uart_validate_session_payload(message, payload, (uint16_t)length) != 0)
        return MCU_CONFIG_COLLECTION_INVALID;
    if (collection->boot_id == 0u || ecobin_uart_read_u64_be(payload + FIELD(TARGET_MCU_BOOT_ID)) != collection->boot_id)
        return MCU_CONFIG_COLLECTION_WRONG_BOOT;
    if (ecobin_uart_bytes_zero(payload + FIELD(CONTENT_SHA256), 32u)
        || ecobin_uart_bytes_zero(payload + FIELD(MCU_PAYLOAD_SHA256), 32u)) return MCU_CONFIG_COLLECTION_INVALID;
    if (message == ECOBIN_UART_MESSAGE_CONFIG_BEGIN) {
        port = payload[FIELD(EXPECTED_PORT_COUNT)];
        if (port != collection->port_capacity) return MCU_CONFIG_COLLECTION_PORT_UNSUPPORTED;
        if (collection->received_parts != 0u)
            return same_set(collection, payload) ? MCU_CONFIG_COLLECTION_DUPLICATE : MCU_CONFIG_COLLECTION_CONFLICT;
        memcpy(collection->application_uid, payload + FIELD(APPLICATION_UID), 16u);
        memcpy(collection->expected_digest, payload + FIELD(MCU_PAYLOAD_SHA256), 32u);
        memcpy(collection->preimage, domain, sizeof(domain));
        memcpy(collection->preimage + ECOBIN_UART_CONFIG_PREIMAGE_VERSION_OFFSET, payload + FIELD(CONFIG_VERSION), 8u);
        memcpy(collection->preimage + ECOBIN_UART_CONFIG_PREIMAGE_CONTENT_SHA256_OFFSET, payload + FIELD(CONTENT_SHA256), 32u);
        collection->preimage[ECOBIN_UART_CONFIG_PREIMAGE_PORT_COUNT_OFFSET] = port;
        collection->expected_ports = port;
        collection->part_count = payload[FIELD(PART_COUNT)];
        collection->received_parts = 1u;
        return MCU_CONFIG_COLLECTION_STAGED;
    }
    if (collection->received_parts == 0u) return MCU_CONFIG_COLLECTION_INCOMPLETE;
    if (!same_set(collection, payload)) return MCU_CONFIG_COLLECTION_CONFLICT;
    part = payload[FIELD(PART_INDEX)];
    bit = (uint16_t)(1u << (part - 1u));
    if (message == ECOBIN_UART_MESSAGE_CONFIG_COMMIT) {
        if (collection->complete) return MCU_CONFIG_COLLECTION_DUPLICATE;
        required = (uint16_t)((1u << (collection->part_count - 1u)) - 1u);
        if ((collection->received_parts & required) != required) return MCU_CONFIG_COLLECTION_INCOMPLETE;
        ecobin_uart_sha256(collection->preimage, preimage_length(collection), digest);
        if (memcmp(digest, collection->expected_digest, sizeof(digest)) != 0) return MCU_CONFIG_COLLECTION_DIGEST_MISMATCH;
        collection->received_parts |= bit;
        collection->complete = 1u;
        return MCU_CONFIG_COLLECTION_COMPLETE;
    }
    if (message == ECOBIN_UART_MESSAGE_CONFIG_DEVICE_BLOCK) {
        start = ECOBIN_UART_CONFIG_PREIMAGE_DEVICE_OFFSET;
        count = ECOBIN_UART_CONFIG_DEVICE_SEMANTIC_LENGTH;
        payload += ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET;
    } else {
        port = payload[ECOBIN_UART_CONFIG_PORT_BLOCK_PORT_NO_OFFSET];
        if (port > collection->expected_ports) return MCU_CONFIG_COLLECTION_PORT_UNSUPPORTED;
        start = ECOBIN_UART_CONFIG_PREIMAGE_PORTS_OFFSET + (port - 1u) * ECOBIN_UART_CONFIG_PORT_SEMANTIC_LENGTH;
        count = ECOBIN_UART_CONFIG_PORT_SEMANTIC_LENGTH;
        payload += ECOBIN_UART_CONFIG_PORT_BLOCK_PORT_NO_OFFSET;
    }
    if (collection->received_parts & bit)
        return memcmp(collection->preimage + start, payload, count) == 0
            ? MCU_CONFIG_COLLECTION_DUPLICATE : MCU_CONFIG_COLLECTION_CONFLICT;
    memcpy(collection->preimage + start, payload, count);
    collection->received_parts |= bit;
    return MCU_CONFIG_COLLECTION_STAGED;
}

size_t McuConfigCollection_CopyComplete(const McuConfigCollection *collection, uint8_t *output, size_t capacity) {
    size_t length;
    if (collection == NULL || output == NULL || !collection->complete) return 0u;
    length = preimage_length(collection);
    if (capacity < length) return 0u;
    memcpy(output, collection->preimage, length);
    return length;
}

#define DEVICE(name) (device + ECOBIN_UART_CONFIG_DEVICE_BLOCK_##name##_OFFSET - ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET)
#define PORT(name) (port + ECOBIN_UART_CONFIG_PORT_BLOCK_##name##_OFFSET - ECOBIN_UART_CONFIG_PORT_BLOCK_PORT_NO_OFFSET)

uint8_t McuConfigCollection_ReadFullnessPolicy(const McuConfigCollection *collection,
    uint8_t port_no, McuConfigFullnessPolicy *output) {
    const uint8_t *port;
    if (collection == NULL || output == NULL || !collection->complete
        || port_no == 0u || port_no > collection->expected_ports) return 0u;
    port = collection->preimage + ECOBIN_UART_CONFIG_PREIMAGE_PORTS_OFFSET
        + (port_no - 1u) * ECOBIN_UART_CONFIG_PORT_SEMANTIC_LENGTH;
    memset(output, 0, sizeof(*output));
    output->config_version = ecobin_uart_read_u64_be(collection->preimage + ECOBIN_UART_CONFIG_PREIMAGE_VERSION_OFFSET);
    output->echo_timeout_us = ecobin_uart_read_u32_be(PORT(FULLNESS_ECHO_TIMEOUT_US));
    output->settle_wait_ms = ecobin_uart_read_u32_be(PORT(FULLNESS_SETTLE_WAIT_MS));
    output->distance_threshold_mm = ecobin_uart_read_u32_be(PORT(FULLNESS_DISTANCE_THRESHOLD_MM));
    output->sample_count = *PORT(FULLNESS_SAMPLE_COUNT);
    output->minimum_valid_count = *PORT(FULLNESS_MINIMUM_VALID_SAMPLE_COUNT);
    output->port_no = *PORT(PORT_NO);
    output->enabled = *PORT(ENABLED);
    output->sensor_kind = *PORT(FULLNESS_SENSOR_KIND);
    return 1u;
}

uint8_t McuConfigCollection_ReadWeightPolicy(const McuConfigCollection *collection,
    uint8_t port_no, McuConfigWeightPolicy *output) {
    const uint8_t *device, *port;
    if (collection == NULL || output == NULL || !collection->complete
        || port_no == 0u || port_no > collection->expected_ports) return 0u;
    device = collection->preimage + ECOBIN_UART_CONFIG_PREIMAGE_DEVICE_OFFSET;
    port = collection->preimage + ECOBIN_UART_CONFIG_PREIMAGE_PORTS_OFFSET
        + (port_no - 1u) * ECOBIN_UART_CONFIG_PORT_SEMANTIC_LENGTH;
    memset(output, 0, sizeof(*output));
    output->config_version = ecobin_uart_read_u64_be(collection->preimage + ECOBIN_UART_CONFIG_PREIMAGE_VERSION_OFFSET);
    output->calibration_version = ecobin_uart_read_u32_be(PORT(CALIBRATION_VERSION));
    output->poll_interval_ms = ecobin_uart_read_u32_be(DEVICE(WEIGHT_POLL_INTERVAL_MS));
    output->response_timeout_ms = ecobin_uart_read_u32_be(DEVICE(WEIGHT_RESPONSE_TIMEOUT_MS));
    output->measurement.timeout_ms = ecobin_uart_read_u32_be(PORT(WEIGHT_MEASUREMENT_TIMEOUT_MS));
    output->measurement.stable_window_ms = ecobin_uart_read_u32_be(PORT(WEIGHT_STABLE_WINDOW_MS));
    output->measurement.maximum_age_ms = ecobin_uart_read_u32_be(PORT(WEIGHT_MAXIMUM_SAMPLE_AGE_MS));
    output->measurement.maximum_span_grams = ecobin_uart_read_u32_be(PORT(WEIGHT_MAXIMUM_FLUCTUATION_GRAMS));
    output->measurement.stable_samples = (uint8_t)ecobin_uart_read_u16_be(PORT(WEIGHT_REQUIRED_SAMPLE_COUNT));
    output->measurement.minimum_median_samples = *PORT(WEIGHT_MINIMUM_MEDIAN_SAMPLE_COUNT);
    output->measurement.minimum_grams = ecobin_uart_read_i32_be(PORT(WEIGHT_MINIMUM_GRAMS));
    output->measurement.maximum_grams = ecobin_uart_read_i32_be(PORT(WEIGHT_MAXIMUM_GRAMS));
    output->port_no = *PORT(PORT_NO);
    output->enabled = *PORT(ENABLED);
    return 1u;
}
