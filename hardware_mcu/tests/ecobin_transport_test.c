#include <stdio.h>
#include <string.h>

#include "ecobin_transport.h"

#define CAPTURED_FRAME_LIMIT 64u

static uint8_t captured_frames[CAPTURED_FRAME_LIMIT][ECOBIN_UART_MAX_FRAME_LENGTH];
static uint16_t captured_lengths[CAPTURED_FRAME_LIMIT];
static size_t captured_count;
static uint32_t edge_tx_sequence = 1u;

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            fprintf(stderr, "CHECK failed at line %d: %s\n",                 \
                    __LINE__, #condition);                                     \
            return 1;                                                          \
        }                                                                      \
    } while (0)

void ecobin_transport_host_send(const uint8_t *data, uint16_t length)
{
    if (captured_count >= CAPTURED_FRAME_LIMIT
        || length > ECOBIN_UART_MAX_FRAME_LENGTH) {
        fprintf(stderr, "capture overflow\n");
        return;
    }
    memcpy(captured_frames[captured_count], data, length);
    captured_lengths[captured_count] = length;
    captured_count++;
}

static int captured_view(size_t index, ecobin_uart_frame_view_t *view)
{
    if (index >= captured_count) return -100;
    return ecobin_uart_validate_frame(
        captured_frames[index],
        captured_lengths[index],
        ECOBIN_UART_SENDER_ROLE_MCU,
        view);
}

static int feed_edge_frame(
    uint8_t message_type,
    const uint8_t *payload,
    uint16_t payload_length)
{
    uint8_t frame[ECOBIN_UART_MAX_FRAME_LENGTH];
    size_t frame_length;
    uint8_t flags;
    size_t index;
    ecobin_transport_rx_msg_t rx_msg;
    int result;

    flags = ecobin_uart_message_ack_required(message_type)
        ? ECOBIN_UART_FLAG_ACK_REQUIRED
        : 0u;
    result = ecobin_uart_encode_frame(
        message_type,
        flags,
        edge_tx_sequence++,
        payload,
        payload_length,
        frame,
        sizeof(frame),
        &frame_length);
    if (result != 0) return result;
    for (index = 0u; index < frame_length; ++index) {
        ecobin_transport_feed_byte(frame[index]);
    }
    while (ecobin_transport_poll(&rx_msg) != 0) {
        /* Handshake/config/query are consumed inside the transport layer. */
    }
    return 0;
}

static void fill_bytes(uint8_t *target, size_t length, uint8_t first)
{
    size_t index;
    for (index = 0u; index < length; ++index) {
        target[index] = (uint8_t)(first + index);
    }
}

static void write_command_digest(
    uint8_t message_type,
    uint8_t *payload,
    uint16_t payload_length)
{
    static const uint8_t domain[] = {
        0x45u, 0x43u, 0x4Fu, 0x42u, 0x49u, 0x4Eu, 0x3Au, 0x55u,
        0x41u, 0x52u, 0x54u, 0x3Au, 0x43u, 0x4Fu, 0x4Du, 0x4Du,
        0x41u, 0x4Eu, 0x44u, 0x3Au, 0x76u, 0x31u, 0x00u
    };
    ecobin_uart_sha256_context_t context;
    uint8_t semantic_length[2];
    uint16_t semantic_size;

    semantic_size = (uint16_t)(payload_length - 48u);
    ecobin_uart_write_u16_be(semantic_length, semantic_size);
    ecobin_uart_sha256_init(&context);
    ecobin_uart_sha256_update(&context, domain, sizeof(domain));
    ecobin_uart_sha256_update(&context, &message_type, 1u);
    ecobin_uart_sha256_update(&context, semantic_length, 2u);
    ecobin_uart_sha256_update(
        &context,
        payload + 48u,
        semantic_size);
    ecobin_uart_sha256_final(&context, payload + 16u);
}

static void build_edge_hello(uint8_t *payload, uint16_t *payload_length)
{
    static const char identity[] = "orangepi-hil";
    static const char version[] = "1.0.0-test";
    uint16_t offset;

    memset(payload, 0, ECOBIN_UART_HELLO_PAYLOAD_MAX_LENGTH);
    payload[ECOBIN_UART_HELLO_SENDER_ROLE_OFFSET] =
        ECOBIN_UART_SENDER_ROLE_EDGE;
    fill_bytes(payload + ECOBIN_UART_HELLO_SENDER_BOOT_ID_OFFSET, 8u, 0xA1u);
    payload[ECOBIN_UART_HELLO_SUPPORTED_MAJOR_OFFSET] =
        ECOBIN_UART_PROTOCOL_MAJOR;
    payload[ECOBIN_UART_HELLO_MINIMUM_MINOR_OFFSET] =
        ECOBIN_UART_PROTOCOL_MINOR;
    payload[ECOBIN_UART_HELLO_MAXIMUM_MINOR_OFFSET] =
        ECOBIN_UART_PROTOCOL_MINOR;
    payload[ECOBIN_UART_HELLO_PORT_COUNT_OFFSET] = 1u;
    ecobin_uart_write_u64_be(
        payload + ECOBIN_UART_HELLO_CAPABILITY_BITMAP_OFFSET,
        UINT64_C(0x1FFF));
    ecobin_uart_write_u16_be(
        payload + ECOBIN_UART_HELLO_MAXIMUM_FRAME_LENGTH_OFFSET,
        ECOBIN_UART_MAX_FRAME_LENGTH);
    offset = ECOBIN_UART_HELLO_FIRMWARE_IDENTITY_OFFSET;
    payload[offset++] = (uint8_t)(sizeof(identity) - 1u);
    memcpy(payload + offset, identity, sizeof(identity) - 1u);
    offset = (uint16_t)(offset + sizeof(identity) - 1u);
    payload[offset++] = (uint8_t)(sizeof(version) - 1u);
    memcpy(payload + offset, version, sizeof(version) - 1u);
    offset = (uint16_t)(offset + sizeof(version) - 1u);
    *payload_length = offset;
}

static void build_edge_hello_ack(
    uint8_t payload[ECOBIN_UART_HELLO_ACK_PAYLOAD_MAX_LENGTH])
{
    memset(payload, 0, ECOBIN_UART_HELLO_ACK_PAYLOAD_MAX_LENGTH);
    fill_bytes(
        payload + ECOBIN_UART_HELLO_ACK_RESPONDER_BOOT_ID_OFFSET,
        8u,
        0xA1u);
    fill_bytes(
        payload + ECOBIN_UART_HELLO_ACK_REFERENCED_SENDER_BOOT_ID_OFFSET,
        8u,
        1u);
    payload[ECOBIN_UART_HELLO_ACK_SELECTED_MAJOR_OFFSET] =
        ECOBIN_UART_PROTOCOL_MAJOR;
    payload[ECOBIN_UART_HELLO_ACK_SELECTED_MINOR_OFFSET] =
        ECOBIN_UART_PROTOCOL_MINOR;
    payload[ECOBIN_UART_HELLO_ACK_STATUS_OFFSET] =
        ECOBIN_UART_HELLO_STATUS_ACCEPTED;
    payload[ECOBIN_UART_HELLO_ACK_PORT_COUNT_OFFSET] = 1u;
    ecobin_uart_write_u64_be(
        payload + ECOBIN_UART_HELLO_ACK_CAPABILITY_BITMAP_OFFSET,
        ECOBIN_UART_CAPABILITY_CONFIG_STAGING_COMMIT
            | ECOBIN_UART_CAPABILITY_STATE_SNAPSHOT_SEGMENTS);
    ecobin_uart_write_u16_be(
        payload + ECOBIN_UART_HELLO_ACK_MAXIMUM_FRAME_LENGTH_OFFSET,
        ECOBIN_UART_MAX_FRAME_LENGTH);
    ecobin_uart_write_u16_be(
        payload + ECOBIN_UART_HELLO_ACK_ERROR_CODE_OFFSET,
        ECOBIN_UART_NACK_ERROR_NONE);
}

static void build_query(
    uint8_t payload[ECOBIN_UART_QUERY_STATE_PAYLOAD_MAX_LENGTH],
    uint8_t uid_start,
    uint8_t snapshot_start)
{
    memset(payload, 0, ECOBIN_UART_QUERY_STATE_PAYLOAD_MAX_LENGTH);
    fill_bytes(
        payload + ECOBIN_UART_QUERY_STATE_MCU_COMMAND_UID_OFFSET,
        16u,
        uid_start);
    fill_bytes(
        payload + ECOBIN_UART_QUERY_STATE_SNAPSHOT_UID_OFFSET,
        16u,
        snapshot_start);
    write_command_digest(
        ECOBIN_UART_MESSAGE_QUERY_STATE,
        payload,
        ECOBIN_UART_QUERY_STATE_PAYLOAD_MAX_LENGTH);
}

static void compute_mcu_payload_sha256(
    uint8_t digest[32],
    uint64_t version,
    const uint8_t content_sha256[32],
    const uint8_t *device_payload,
    const uint8_t *port_payload)
{
    static const uint8_t domain[] = {
        0x45u, 0x43u, 0x4Fu, 0x42u, 0x49u, 0x4Eu, 0x3Au, 0x55u,
        0x41u, 0x52u, 0x54u, 0x3Au, 0x4Du, 0x43u, 0x55u, 0x2Du,
        0x43u, 0x4Fu, 0x4Eu, 0x46u, 0x49u, 0x47u, 0x3Au, 0x76u,
        0x31u, 0x00u
    };
    ecobin_uart_sha256_context_t context;
    uint8_t version_bytes[8];
    uint8_t port_count;

    ecobin_uart_write_u64_be(version_bytes, version);
    port_count = 1u;
    ecobin_uart_sha256_init(&context);
    ecobin_uart_sha256_update(&context, domain, sizeof(domain));
    ecobin_uart_sha256_update(&context, version_bytes, sizeof(version_bytes));
    ecobin_uart_sha256_update(&context, content_sha256, 32u);
    ecobin_uart_sha256_update(&context, &port_count, 1u);
    ecobin_uart_sha256_update(
        &context,
        device_payload
            + ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET,
        ECOBIN_UART_CONFIG_DEVICE_BLOCK_PAYLOAD_MAX_LENGTH
            - ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET);
    ecobin_uart_sha256_update(
        &context,
        port_payload + ECOBIN_UART_CONFIG_PORT_BLOCK_PORT_NO_OFFSET,
        ECOBIN_UART_CONFIG_PORT_BLOCK_PAYLOAD_MAX_LENGTH
            - ECOBIN_UART_CONFIG_PORT_BLOCK_PORT_NO_OFFSET);
    ecobin_uart_sha256_final(&context, digest);
}

static void set_config_common(
    uint8_t message_type,
    uint8_t *payload,
    uint16_t payload_length,
    uint8_t command_uid_start,
    const uint8_t application_uid[16],
    uint64_t version,
    const uint8_t content_sha256[32],
    const uint8_t mcu_payload_sha256[32],
    uint8_t part_index)
{
    fill_bytes(payload, 16u, command_uid_start);
    memcpy(
        payload + ECOBIN_UART_CONFIG_BEGIN_APPLICATION_UID_OFFSET,
        application_uid,
        16u);
    ecobin_uart_write_u64_be(
        payload + ECOBIN_UART_CONFIG_BEGIN_CONFIG_VERSION_OFFSET,
        version);
    memcpy(
        payload + ECOBIN_UART_CONFIG_BEGIN_CONTENT_SHA256_OFFSET,
        content_sha256,
        32u);
    memcpy(
        payload + ECOBIN_UART_CONFIG_BEGIN_MCU_PAYLOAD_SHA256_OFFSET,
        mcu_payload_sha256,
        32u);
    payload[136u] = part_index;
    payload[137u] = 4u;
    write_command_digest(message_type, payload, payload_length);
}

static void build_config_payloads(
    uint8_t begin[ECOBIN_UART_CONFIG_BEGIN_PAYLOAD_MAX_LENGTH],
    uint8_t device[ECOBIN_UART_CONFIG_DEVICE_BLOCK_PAYLOAD_MAX_LENGTH],
    uint8_t port[ECOBIN_UART_CONFIG_PORT_BLOCK_PAYLOAD_MAX_LENGTH],
    uint8_t commit[ECOBIN_UART_CONFIG_COMMIT_PAYLOAD_MAX_LENGTH],
    uint64_t version)
{
    uint8_t application_uid[16];
    uint8_t content_sha256[32];
    uint8_t mcu_payload_sha256[32];

    memset(begin, 0, ECOBIN_UART_CONFIG_BEGIN_PAYLOAD_MAX_LENGTH);
    memset(device, 0, ECOBIN_UART_CONFIG_DEVICE_BLOCK_PAYLOAD_MAX_LENGTH);
    memset(port, 0, ECOBIN_UART_CONFIG_PORT_BLOCK_PAYLOAD_MAX_LENGTH);
    memset(commit, 0, ECOBIN_UART_CONFIG_COMMIT_PAYLOAD_MAX_LENGTH);
    fill_bytes(application_uid, sizeof(application_uid), 0x31u);
    fill_bytes(content_sha256, sizeof(content_sha256), 0x51u);

    ecobin_uart_write_u32_be(
        device
            + ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET,
        2000u);
    ecobin_uart_write_u32_be(
        device
            + ECOBIN_UART_CONFIG_DEVICE_BLOCK_NEGATIVE_WEIGHT_THRESHOLD_GRAMS_OFFSET,
        100u);
    ecobin_uart_write_u32_be(
        device + ECOBIN_UART_CONFIG_DEVICE_BLOCK_DELIVERY_AUTO_CLOSE_MS_OFFSET,
        3000u);
    ecobin_uart_write_u32_be(
        device
            + ECOBIN_UART_CONFIG_DEVICE_BLOCK_WEIGHT_MEASUREMENT_TIMEOUT_MS_OFFSET,
        2000u);
    ecobin_uart_write_u32_be(
        device
            + ECOBIN_UART_CONFIG_DEVICE_BLOCK_CLEAN_SOLENOID_PULSE_MS_OFFSET,
        500u);
    device[
        ECOBIN_UART_CONFIG_DEVICE_BLOCK_SMOKE_MONITORING_ENABLED_OFFSET] = 1u;

    port[ECOBIN_UART_CONFIG_PORT_BLOCK_PORT_NO_OFFSET] = 1u;
    port[ECOBIN_UART_CONFIG_PORT_BLOCK_ENABLED_OFFSET] = 1u;
    ecobin_uart_write_u32_be(
        port + ECOBIN_UART_CONFIG_PORT_BLOCK_UNIT_PRICE_TEN_THOUSANDTHS_OFFSET,
        1000u);
    port[ECOBIN_UART_CONFIG_PORT_BLOCK_FULLNESS_MODE_OFFSET] =
        ECOBIN_UART_FULLNESS_MODE_INFRARED_OR_WEIGHT;
    ecobin_uart_write_u32_be(
        port
            + ECOBIN_UART_CONFIG_PORT_BLOCK_CONFIGURED_FULL_WEIGHT_GRAMS_OFFSET,
        10000u);
    ecobin_uart_write_u32_be(
        port + ECOBIN_UART_CONFIG_PORT_BLOCK_FULLNESS_SETTLE_WAIT_MS_OFFSET,
        1000u);
    ecobin_uart_write_u32_be(
        port
            + ECOBIN_UART_CONFIG_PORT_BLOCK_FULLNESS_CONFIRMATION_WAIT_MS_OFFSET,
        1000u);
    ecobin_uart_write_u32_be(
        port + ECOBIN_UART_CONFIG_PORT_BLOCK_WEIGHT_STABLE_WINDOW_MS_OFFSET,
        1000u);
    ecobin_uart_write_u32_be(
        port
            + ECOBIN_UART_CONFIG_PORT_BLOCK_WEIGHT_MAXIMUM_FLUCTUATION_GRAMS_OFFSET,
        100u);
    ecobin_uart_write_u16_be(
        port
            + ECOBIN_UART_CONFIG_PORT_BLOCK_WEIGHT_REQUIRED_SAMPLE_COUNT_OFFSET,
        3u);
    ecobin_uart_write_u32_be(
        port
            + ECOBIN_UART_CONFIG_PORT_BLOCK_WEIGHT_MEASUREMENT_TIMEOUT_MS_OFFSET,
        2000u);
    ecobin_uart_write_i32_be(
        port + ECOBIN_UART_CONFIG_PORT_BLOCK_WEIGHT_MINIMUM_GRAMS_OFFSET,
        -1000);
    ecobin_uart_write_i32_be(
        port + ECOBIN_UART_CONFIG_PORT_BLOCK_WEIGHT_MAXIMUM_GRAMS_OFFSET,
        100000);
    ecobin_uart_write_u32_be(
        port + ECOBIN_UART_CONFIG_PORT_BLOCK_CALIBRATION_VERSION_OFFSET,
        1u);
    ecobin_uart_write_u32_be(
        port + ECOBIN_UART_CONFIG_PORT_BLOCK_INFRARED_SAMPLE_TIMEOUT_MS_OFFSET,
        1000u);
    ecobin_uart_write_u32_be(
        port
            + ECOBIN_UART_CONFIG_PORT_BLOCK_DELIVERY_DOOR_OPERATION_TIMEOUT_MS_OFFSET,
        3000u);

    compute_mcu_payload_sha256(
        mcu_payload_sha256,
        version,
        content_sha256,
        device,
        port);
    set_config_common(
        ECOBIN_UART_MESSAGE_CONFIG_BEGIN,
        begin,
        ECOBIN_UART_CONFIG_BEGIN_PAYLOAD_MAX_LENGTH,
        0x11u,
        application_uid,
        version,
        content_sha256,
        mcu_payload_sha256,
        1u);
    begin[ECOBIN_UART_CONFIG_BEGIN_EXPECTED_PORT_COUNT_OFFSET] = 1u;
    write_command_digest(
        ECOBIN_UART_MESSAGE_CONFIG_BEGIN,
        begin,
        ECOBIN_UART_CONFIG_BEGIN_PAYLOAD_MAX_LENGTH);
    set_config_common(
        ECOBIN_UART_MESSAGE_CONFIG_DEVICE_BLOCK,
        device,
        ECOBIN_UART_CONFIG_DEVICE_BLOCK_PAYLOAD_MAX_LENGTH,
        0x21u,
        application_uid,
        version,
        content_sha256,
        mcu_payload_sha256,
        2u);
    set_config_common(
        ECOBIN_UART_MESSAGE_CONFIG_PORT_BLOCK,
        port,
        ECOBIN_UART_CONFIG_PORT_BLOCK_PAYLOAD_MAX_LENGTH,
        0x41u,
        application_uid,
        version,
        content_sha256,
        mcu_payload_sha256,
        3u);
    set_config_common(
        ECOBIN_UART_MESSAGE_CONFIG_COMMIT,
        commit,
        ECOBIN_UART_CONFIG_COMMIT_PAYLOAD_MAX_LENGTH,
        0x61u,
        application_uid,
        version,
        content_sha256,
        mcu_payload_sha256,
        4u);
}

static void compute_snapshot_digest(
    uint8_t digest[32],
    const ecobin_uart_frame_view_t *begin,
    const ecobin_uart_frame_view_t *port,
    const ecobin_uart_frame_view_t *end)
{
    static const uint8_t domain[] = {
        0x45u, 0x43u, 0x4Fu, 0x42u, 0x49u, 0x4Eu, 0x3Au, 0x55u,
        0x41u, 0x52u, 0x54u, 0x3Au, 0x53u, 0x4Eu, 0x41u, 0x50u,
        0x53u, 0x48u, 0x4Fu, 0x54u, 0x3Au, 0x76u, 0x31u, 0x00u
    };
    ecobin_uart_sha256_context_t context;

    ecobin_uart_sha256_init(&context);
    ecobin_uart_sha256_update(&context, domain, sizeof(domain));
    ecobin_uart_sha256_update(
        &context,
        begin->payload + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_SNAPSHOT_UID_OFFSET,
        ECOBIN_UART_STATE_SNAPSHOT_BEGIN_PART_INDEX_OFFSET
            - ECOBIN_UART_STATE_SNAPSHOT_BEGIN_SNAPSHOT_UID_OFFSET);
    ecobin_uart_sha256_update(
        &context,
        begin->payload + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_RESET_REASON_OFFSET,
        1u);
    ecobin_uart_sha256_update(
        &context,
        port->payload + ECOBIN_UART_STATE_SNAPSHOT_PORT_PORT_NO_OFFSET,
        ECOBIN_UART_STATE_SNAPSHOT_PORT_PAYLOAD_MAX_LENGTH
            - ECOBIN_UART_STATE_SNAPSHOT_PORT_PORT_NO_OFFSET);
    ecobin_uart_sha256_update(
        &context,
        end->payload
            + ECOBIN_UART_STATE_SNAPSHOT_END_PENDING_CRITICAL_EVENT_COUNT_OFFSET,
        ECOBIN_UART_STATE_SNAPSHOT_END_SNAPSHOT_SHA256_OFFSET
            - ECOBIN_UART_STATE_SNAPSHOT_END_PENDING_CRITICAL_EVENT_COUNT_OFFSET);
    ecobin_uart_sha256_final(&context, digest);
}

static int check_ack(
    size_t frame_index,
    uint8_t referenced_message_type,
    uint8_t disposition)
{
    ecobin_uart_frame_view_t view;
    CHECK(captured_view(frame_index, &view) == 0);
    CHECK(view.message_type == ECOBIN_UART_MESSAGE_ACK);
    CHECK(
        view.payload[ECOBIN_UART_ACK_REFERENCED_MESSAGE_TYPE_OFFSET]
        == referenced_message_type);
    CHECK(
        view.payload[ECOBIN_UART_ACK_DISPOSITION_OFFSET]
        == disposition);
    return 0;
}

int main(void)
{
    uint8_t edge_hello[ECOBIN_UART_HELLO_PAYLOAD_MAX_LENGTH];
    uint8_t edge_hello_ack[ECOBIN_UART_HELLO_ACK_PAYLOAD_MAX_LENGTH];
    uint8_t query[ECOBIN_UART_QUERY_STATE_PAYLOAD_MAX_LENGTH];
    uint8_t begin[ECOBIN_UART_CONFIG_BEGIN_PAYLOAD_MAX_LENGTH];
    uint8_t device[ECOBIN_UART_CONFIG_DEVICE_BLOCK_PAYLOAD_MAX_LENGTH];
    uint8_t port[ECOBIN_UART_CONFIG_PORT_BLOCK_PAYLOAD_MAX_LENGTH];
    uint8_t commit[ECOBIN_UART_CONFIG_COMMIT_PAYLOAD_MAX_LENGTH];
    uint8_t snapshot_digest[32];
    uint16_t edge_hello_length;
    size_t index;
    size_t before;
    uint32_t config_result_event_sequence;
    ecobin_uart_frame_view_t hello_view;
    ecobin_uart_frame_view_t view;
    ecobin_uart_frame_view_t snapshot_begin;
    ecobin_uart_frame_view_t snapshot_port;
    ecobin_uart_frame_view_t snapshot_end;
    ecobin_transport_rx_msg_t rx_msg;

    ecobin_transport_init("stm32f103rct6", "1.0.0-hil.1");
    CHECK(captured_count == 1u);
    CHECK(captured_view(0u, &hello_view) == 0);
    CHECK(hello_view.message_type == ECOBIN_UART_MESSAGE_HELLO);
    CHECK(
        hello_view.payload[ECOBIN_UART_HELLO_SENDER_ROLE_OFFSET]
        == ECOBIN_UART_SENDER_ROLE_MCU);
    CHECK(
        ecobin_uart_read_u64_be(
            hello_view.payload + ECOBIN_UART_HELLO_CAPABILITY_BITMAP_OFFSET)
        == (ECOBIN_UART_CAPABILITY_CONFIG_STAGING_COMMIT
            | ECOBIN_UART_CAPABILITY_STATE_SNAPSHOT_SEGMENTS));
    index = ECOBIN_UART_HELLO_FIRMWARE_IDENTITY_OFFSET;
    CHECK(hello_view.payload[index] == strlen("stm32f103rct6"));
    index += 1u + hello_view.payload[index];
    CHECK(hello_view.payload[index] == strlen("1.0.0-hil.1"));
    CHECK(
        hello_view.payload_length
        == index + 1u + hello_view.payload[index]);

    for (index = 0u; index < 60u; ++index) {
        ecobin_transport_tick_100ms();
        CHECK(ecobin_transport_poll(&rx_msg) == 0);
    }
    CHECK(captured_count == 7u);
    for (index = 0u; index < captured_count; ++index) {
        CHECK(captured_view(index, &view) == 0);
        CHECK(view.message_type == ECOBIN_UART_MESSAGE_HELLO);
    }

    build_edge_hello(edge_hello, &edge_hello_length);
    before = captured_count;
    CHECK(
        feed_edge_frame(
            ECOBIN_UART_MESSAGE_HELLO,
            edge_hello,
            edge_hello_length) == 0);
    CHECK(captured_count == before + 2u);
    CHECK(captured_view(before, &view) == 0);
    CHECK(view.message_type == ECOBIN_UART_MESSAGE_HELLO);
    CHECK(captured_view(before + 1u, &view) == 0);
    CHECK(view.message_type == ECOBIN_UART_MESSAGE_HELLO_ACK);
    CHECK(
        view.payload[ECOBIN_UART_HELLO_ACK_STATUS_OFFSET]
        == ECOBIN_UART_HELLO_STATUS_ACCEPTED);
    CHECK(ecobin_transport_get_state() == ECOBIN_TRANSPORT_HELLO_SENT);

    build_edge_hello_ack(edge_hello_ack);
    CHECK(
        feed_edge_frame(
            ECOBIN_UART_MESSAGE_HELLO_ACK,
            edge_hello_ack,
            ECOBIN_UART_HELLO_ACK_PAYLOAD_MAX_LENGTH) == 0);
    CHECK(ecobin_transport_get_state() == ECOBIN_TRANSPORT_READY);

    build_query(query, 0x71u, 0x91u);
    before = captured_count;
    CHECK(
        feed_edge_frame(
            ECOBIN_UART_MESSAGE_QUERY_STATE,
            query,
            ECOBIN_UART_QUERY_STATE_PAYLOAD_MAX_LENGTH) == 0);
    CHECK(captured_count == before + 4u);
    CHECK(check_ack(
        before,
        ECOBIN_UART_MESSAGE_QUERY_STATE,
        ECOBIN_UART_ACK_DISPOSITION_ACCEPTED) == 0);
    CHECK(captured_view(before + 1u, &snapshot_begin) == 0);
    CHECK(captured_view(before + 2u, &snapshot_port) == 0);
    CHECK(captured_view(before + 3u, &snapshot_end) == 0);
    CHECK(
        snapshot_begin.message_type
        == ECOBIN_UART_MESSAGE_STATE_SNAPSHOT_BEGIN);
    CHECK(
        snapshot_port.message_type
        == ECOBIN_UART_MESSAGE_STATE_SNAPSHOT_PORT);
    CHECK(
        snapshot_end.message_type
        == ECOBIN_UART_MESSAGE_STATE_SNAPSHOT_END);
    CHECK(
        memcmp(
            snapshot_begin.payload
                + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_SNAPSHOT_UID_OFFSET,
            query + ECOBIN_UART_QUERY_STATE_SNAPSHOT_UID_OFFSET,
            16u) == 0);
    compute_snapshot_digest(
        snapshot_digest,
        &snapshot_begin,
        &snapshot_port,
        &snapshot_end);
    CHECK(
        memcmp(
            snapshot_digest,
            snapshot_end.payload
                + ECOBIN_UART_STATE_SNAPSHOT_END_SNAPSHOT_SHA256_OFFSET,
            32u) == 0);

    build_config_payloads(begin, device, port, commit, UINT64_C(23));
    before = captured_count;
    CHECK(feed_edge_frame(
        ECOBIN_UART_MESSAGE_CONFIG_BEGIN,
        begin,
        ECOBIN_UART_CONFIG_BEGIN_PAYLOAD_MAX_LENGTH) == 0);
    CHECK(captured_count == before + 1u);
    CHECK(check_ack(
        before,
        ECOBIN_UART_MESSAGE_CONFIG_BEGIN,
        ECOBIN_UART_ACK_DISPOSITION_ACCEPTED) == 0);

    before = captured_count;
    CHECK(feed_edge_frame(
        ECOBIN_UART_MESSAGE_CONFIG_DEVICE_BLOCK,
        device,
        ECOBIN_UART_CONFIG_DEVICE_BLOCK_PAYLOAD_MAX_LENGTH) == 0);
    CHECK(captured_count == before + 1u);
    CHECK(check_ack(
        before,
        ECOBIN_UART_MESSAGE_CONFIG_DEVICE_BLOCK,
        ECOBIN_UART_ACK_DISPOSITION_ACCEPTED) == 0);

    before = captured_count;
    CHECK(feed_edge_frame(
        ECOBIN_UART_MESSAGE_CONFIG_PORT_BLOCK,
        port,
        ECOBIN_UART_CONFIG_PORT_BLOCK_PAYLOAD_MAX_LENGTH) == 0);
    CHECK(captured_count == before + 1u);
    CHECK(check_ack(
        before,
        ECOBIN_UART_MESSAGE_CONFIG_PORT_BLOCK,
        ECOBIN_UART_ACK_DISPOSITION_ACCEPTED) == 0);

    before = captured_count;
    CHECK(feed_edge_frame(
        ECOBIN_UART_MESSAGE_CONFIG_COMMIT,
        commit,
        ECOBIN_UART_CONFIG_COMMIT_PAYLOAD_MAX_LENGTH) == 0);
    CHECK(captured_count == before + 2u);
    CHECK(check_ack(
        before,
        ECOBIN_UART_MESSAGE_CONFIG_COMMIT,
        ECOBIN_UART_ACK_DISPOSITION_ACCEPTED) == 0);
    CHECK(captured_view(before + 1u, &view) == 0);
    CHECK(view.message_type == ECOBIN_UART_MESSAGE_CONFIG_APPLY_RESULT);
    CHECK(
        view.payload[ECOBIN_UART_CONFIG_APPLY_RESULT_STATUS_OFFSET]
        == ECOBIN_UART_CONFIG_APPLY_STATUS_APPLIED);
    CHECK(
        ecobin_uart_read_u64_be(
            view.payload + ECOBIN_UART_CONFIG_APPLY_RESULT_CONFIG_VERSION_OFFSET)
        == UINT64_C(23));
    config_result_event_sequence = ecobin_uart_read_u32_be(
        view.payload
            + ECOBIN_UART_CONFIG_APPLY_RESULT_MCU_EVENT_SEQUENCE_OFFSET);

    before = captured_count;
    CHECK(feed_edge_frame(
        ECOBIN_UART_MESSAGE_CONFIG_COMMIT,
        commit,
        ECOBIN_UART_CONFIG_COMMIT_PAYLOAD_MAX_LENGTH) == 0);
    CHECK(captured_count == before + 2u);
    CHECK(check_ack(
        before,
        ECOBIN_UART_MESSAGE_CONFIG_COMMIT,
        ECOBIN_UART_ACK_DISPOSITION_DUPLICATE_ACCEPTED) == 0);
    CHECK(captured_view(before + 1u, &view) == 0);
    CHECK(view.message_type == ECOBIN_UART_MESSAGE_CONFIG_APPLY_RESULT);
    CHECK(
        ecobin_uart_read_u32_be(
            view.payload
                + ECOBIN_UART_CONFIG_APPLY_RESULT_MCU_EVENT_SEQUENCE_OFFSET)
        == config_result_event_sequence);

    CHECK(feed_edge_frame(
        ECOBIN_UART_MESSAGE_CONFIG_BEGIN,
        begin,
        ECOBIN_UART_CONFIG_BEGIN_PAYLOAD_MAX_LENGTH) == 0);
    CHECK(feed_edge_frame(
        ECOBIN_UART_MESSAGE_CONFIG_DEVICE_BLOCK,
        device,
        ECOBIN_UART_CONFIG_DEVICE_BLOCK_PAYLOAD_MAX_LENGTH) == 0);
    CHECK(feed_edge_frame(
        ECOBIN_UART_MESSAGE_CONFIG_PORT_BLOCK,
        port,
        ECOBIN_UART_CONFIG_PORT_BLOCK_PAYLOAD_MAX_LENGTH) == 0);
    before = captured_count;
    CHECK(feed_edge_frame(
        ECOBIN_UART_MESSAGE_CONFIG_COMMIT,
        commit,
        ECOBIN_UART_CONFIG_COMMIT_PAYLOAD_MAX_LENGTH) == 0);
    CHECK(captured_count == before + 2u);
    CHECK(check_ack(
        before,
        ECOBIN_UART_MESSAGE_CONFIG_COMMIT,
        ECOBIN_UART_ACK_DISPOSITION_DUPLICATE_ACCEPTED) == 0);

    build_query(query, 0xB1u, 0xD1u);
    before = captured_count;
    CHECK(feed_edge_frame(
        ECOBIN_UART_MESSAGE_QUERY_STATE,
        query,
        ECOBIN_UART_QUERY_STATE_PAYLOAD_MAX_LENGTH) == 0);
    CHECK(captured_count == before + 4u);
    CHECK(captured_view(before + 1u, &snapshot_begin) == 0);
    CHECK(
        ecobin_uart_read_u64_be(
            snapshot_begin.payload
                + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_APPLIED_CONFIG_VERSION_OFFSET)
        == UINT64_C(23));
    CHECK(
        snapshot_begin.payload[
            ECOBIN_UART_STATE_SNAPSHOT_BEGIN_STAGING_VALID_OFFSET]
        == 0u);

    printf("ecobin transport host tests passed (%lu frames captured)\n",
           (unsigned long)captured_count);
    return 0;
}
