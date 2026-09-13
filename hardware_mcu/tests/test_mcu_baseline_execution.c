#include <assert.h>
#include <string.h>
#include "mcu_work_preparation.h"
#include "actuator_runtime.h"
#include "runtime_clock.h"

#define BASE(field) ECOBIN_UART_MEASURE_BASELINE_##field##_OFFSET
#define RESULT(field) ECOBIN_UART_BASELINE_MEASUREMENT_RESULT_##field##_OFFSET
#define DECISION(field) ECOBIN_UART_COMMAND_DECISION_##field##_OFFSET
#define DEVICE(field) (ECOBIN_UART_CONFIG_PREIMAGE_DEVICE_OFFSET \
    + ECOBIN_UART_CONFIG_DEVICE_BLOCK_##field##_OFFSET \
    - ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET)
#define PORT(field) (ECOBIN_UART_CONFIG_PREIMAGE_PORTS_OFFSET \
    + ECOBIN_UART_CONFIG_PORT_BLOCK_##field##_OFFSET \
    - ECOBIN_UART_CONFIG_PORT_BLOCK_PORT_NO_OFFSET)

static McuControlEndpoint endpoint;
static McuWorkPreparation preparation;
static uint8_t replies[3][ECOBIN_UART_MAX_FRAME_LENGTH];
static size_t reply_lengths[3];
static uint8_t reply_count;
static uint32_t tx_sequence;
static const uint8_t content_sha256[32] = {
    1u, 2u, 3u, 4u, 5u, 6u, 7u, 8u, 9u, 10u, 11u, 12u, 13u, 14u, 15u, 16u,
    17u, 18u, 19u, 20u, 21u, 22u, 23u, 24u, 25u, 26u, 27u, 28u, 29u, 30u, 31u, 32u
};
static const uint8_t mcu_sha256[32] = {
    32u, 31u, 30u, 29u, 28u, 27u, 26u, 25u, 24u, 23u, 22u, 21u, 20u, 19u, 18u, 17u,
    16u, 15u, 14u, 13u, 12u, 11u, 10u, 9u, 8u, 7u, 6u, 5u, 4u, 3u, 2u, 1u
};

static uint32_t enter(void) { return 0u; }
static void leave(uint32_t mask) { (void)mask; }
static uint8_t pinch(void) { return 0u; }
static void outputs(uint8_t pins) { (void)pins; }
static const ActuatorHardware hardware = {enter, leave, pinch, outputs};

static void sink(const uint8_t *data, size_t length, void *context) {
    (void)context;
    assert(reply_count < 3u && length <= ECOBIN_UART_MAX_FRAME_LENGTH);
    memcpy(replies[reply_count], data, length);
    reply_lengths[reply_count++] = length;
}

static uint16_t guard(uint8_t message, const uint8_t *payload,
    size_t length, uint64_t now, void *context) {
    (void)payload; (void)length; (void)now; (void)context;
    return message == ECOBIN_UART_MESSAGE_MEASURE_BASELINE
        ? ECOBIN_UART_NACK_ERROR_NONE : ECOBIN_UART_NACK_ERROR_UNSUPPORTED_MESSAGE;
}

static void feed(uint8_t message, const uint8_t *payload, uint16_t length, uint64_t now) {
    uint8_t frame[ECOBIN_UART_MAX_FRAME_LENGTH];
    size_t frame_length;
    int required = ecobin_uart_message_ack_required(message);
    assert(required >= 0);
    assert(ecobin_uart_encode_frame(message, required ? ECOBIN_UART_FLAG_ACK_REQUIRED : 0u,
        ++tx_sequence, payload, length, frame, sizeof(frame), &frame_length) == 0);
    reply_count = 0u;
    assert(McuControlEndpoint_Feed(&endpoint, frame, frame_length, now) == 1u);
}

static ecobin_uart_frame_view_t reply(uint8_t index) {
    ecobin_uart_frame_view_t view;
    assert(index < reply_count);
    assert(ecobin_uart_validate_frame(replies[index], reply_lengths[index],
        ECOBIN_UART_SENDER_ROLE_MCU, &view) == 0);
    return view;
}

static void bind_boot(uint64_t boot_id) {
    uint8_t payload[ECOBIN_UART_BIND_BOOT_PAYLOAD_MAX_LENGTH];
    memset(payload, 0, sizeof(payload));
    ecobin_uart_write_u64_be(payload + ECOBIN_UART_BOOT_PROBE_PROBE_ID_OFFSET, 1u);
    feed(ECOBIN_UART_MESSAGE_BOOT_PROBE, payload, ECOBIN_UART_BOOT_PROBE_PAYLOAD_MAX_LENGTH, 0u);
    assert(reply(0u).message_type == ECOBIN_UART_MESSAGE_BOOT_PROBE_REPLY);
    ecobin_uart_write_u64_be(payload + ECOBIN_UART_BIND_BOOT_PROBE_ID_OFFSET, 1u);
    ecobin_uart_write_u64_be(payload + ECOBIN_UART_BIND_BOOT_PROPOSED_MCU_BOOT_ID_OFFSET, boot_id);
    feed(ECOBIN_UART_MESSAGE_BIND_BOOT, payload, ECOBIN_UART_BIND_BOOT_PAYLOAD_MAX_LENGTH, 0u);
    assert(reply(0u).message_type == ECOBIN_UART_MESSAGE_BIND_BOOT_REPLY);
}

static void install_configuration(uint8_t staging) {
    McuConfigCollection *active = &preparation.configuration.active;
    uint8_t *preimage = active->preimage;
    active->complete = 1u;
    active->expected_ports = 1u;
    memcpy(active->expected_digest, mcu_sha256, sizeof(mcu_sha256));
    ecobin_uart_write_u64_be(preimage + ECOBIN_UART_CONFIG_PREIMAGE_VERSION_OFFSET, 8u);
    memcpy(preimage + ECOBIN_UART_CONFIG_PREIMAGE_CONTENT_SHA256_OFFSET,
        content_sha256, sizeof(content_sha256));
    preimage[ECOBIN_UART_CONFIG_PREIMAGE_PORT_COUNT_OFFSET] = 1u;
    ecobin_uart_write_u32_be(preimage + DEVICE(WEIGHT_POLL_INTERVAL_MS), 250u);
    ecobin_uart_write_u32_be(preimage + DEVICE(WEIGHT_RESPONSE_TIMEOUT_MS), 200u);
    preimage[PORT(PORT_NO)] = 1u;
    preimage[PORT(ENABLED)] = 1u;
    ecobin_uart_write_u32_be(preimage + PORT(WEIGHT_STABLE_WINDOW_MS), 1500u);
    ecobin_uart_write_u32_be(preimage + PORT(WEIGHT_MAXIMUM_FLUCTUATION_GRAMS), 100u);
    ecobin_uart_write_u16_be(preimage + PORT(WEIGHT_REQUIRED_SAMPLE_COUNT), 5u);
    ecobin_uart_write_u32_be(preimage + PORT(WEIGHT_MEASUREMENT_TIMEOUT_MS), 5000u);
    ecobin_uart_write_i32_be(preimage + PORT(WEIGHT_MINIMUM_GRAMS), -350000);
    ecobin_uart_write_i32_be(preimage + PORT(WEIGHT_MAXIMUM_GRAMS), 350000);
    ecobin_uart_write_u32_be(preimage + PORT(CALIBRATION_VERSION), 3u);
    ecobin_uart_write_u32_be(preimage + PORT(WEIGHT_MAXIMUM_SAMPLE_AGE_MS), 750u);
    preimage[PORT(WEIGHT_MINIMUM_MEDIAN_SAMPLE_COUNT)] = 5u;
    assert(McuDeviceFacts_PublishConfiguration(&endpoint.facts, 8u,
        content_sha256, mcu_sha256, staging));
}

static void reset_runtime(uint64_t boot_id) {
    RuntimeClock_Init();
    ActuatorRuntime_Init(&hardware);
    McuControlEndpoint_Init(&endpoint, 1u, sink, NULL);
    assert(McuWorkPreparation_Attach(&preparation, &endpoint, 1u, guard, NULL));
    reply_count = 0u;
    tx_sequence = 0u;
    bind_boot(boot_id);
    install_configuration(0u);
}

static void command_digest(uint8_t *payload) {
    static const uint8_t domain[] = {
        69u, 67u, 79u, 66u, 73u, 78u, 58u, 85u, 65u, 82u, 84u, 58u,
        67u, 79u, 77u, 77u, 65u, 78u, 68u, 58u, 118u, 50u, 0u
    };
    ecobin_uart_sha256_context_t context;
    uint8_t suffix[3];
    suffix[0] = ECOBIN_UART_MESSAGE_MEASURE_BASELINE;
    ecobin_uart_write_u16_be(suffix + 1u,
        ECOBIN_UART_MEASURE_BASELINE_PAYLOAD_MAX_LENGTH - 48u);
    ecobin_uart_sha256_init(&context);
    ecobin_uart_sha256_update(&context, domain, sizeof(domain));
    ecobin_uart_sha256_update(&context, suffix, sizeof(suffix));
    ecobin_uart_sha256_update(&context, payload + 48u,
        ECOBIN_UART_MEASURE_BASELINE_PAYLOAD_MAX_LENGTH - 48u);
    ecobin_uart_sha256_final(&context, payload + BASE(COMMAND_DIGEST_SHA256));
}

static void make_command(uint8_t *payload, uint64_t boot_id, uint32_t sequence,
    uint8_t command_tag, uint8_t measurement_tag) {
    memset(payload, 0, ECOBIN_UART_MEASURE_BASELINE_PAYLOAD_MAX_LENGTH);
    memset(payload + BASE(MCU_COMMAND_UID), command_tag, 16u);
    ecobin_uart_write_u64_be(payload + BASE(TARGET_MCU_BOOT_ID), boot_id);
    ecobin_uart_write_u32_be(payload + BASE(COMMAND_SEQUENCE), sequence);
    memset(payload + BASE(MEASUREMENT_UID), measurement_tag, 16u);
    payload[BASE(PORT_NO)] = 1u;
    ecobin_uart_write_u64_be(payload + BASE(CONFIG_VERSION), 8u);
    memcpy(payload + BASE(CONFIG_CONTENT_SHA256), content_sha256, sizeof(content_sha256));
    ecobin_uart_write_u32_be(payload + BASE(START_EXECUTION_WINDOW_MS), 1000u);
    ecobin_uart_write_u32_be(payload + BASE(MEASUREMENT_TIMEOUT_MS), 5000u);
    command_digest(payload);
    assert(ecobin_uart_validate_session_payload(ECOBIN_UART_MESSAGE_MEASURE_BASELINE,
        payload, ECOBIN_UART_MEASURE_BASELINE_PAYLOAD_MAX_LENGTH) == 0);
}

static void accept(const uint8_t *payload, uint64_t now) {
    ecobin_uart_frame_view_t view;
    feed(ECOBIN_UART_MESSAGE_MEASURE_BASELINE, payload,
        ECOBIN_UART_MEASURE_BASELINE_PAYLOAD_MAX_LENGTH, now);
    assert(reply_count == 1u);
    view = reply(0u);
    assert(view.message_type == ECOBIN_UART_MESSAGE_COMMAND_DECISION);
    assert(view.payload[DECISION(OUTCOME)] == ECOBIN_UART_COMMAND_OUTCOME_ACCEPTED);
    assert(ecobin_uart_read_u16_be(view.payload + DECISION(ERROR_CODE)) == ECOBIN_UART_NACK_ERROR_NONE);
}

static void reject_without_baseline(const uint8_t *payload, uint64_t now) {
    ecobin_uart_frame_view_t view;
    feed(ECOBIN_UART_MESSAGE_MEASURE_BASELINE, payload,
        ECOBIN_UART_MEASURE_BASELINE_PAYLOAD_MAX_LENGTH, now);
    assert(reply_count == 1u);
    view = reply(0u);
    assert(view.message_type == ECOBIN_UART_MESSAGE_COMMAND_DECISION);
    assert(view.payload[DECISION(OUTCOME)] == ECOBIN_UART_COMMAND_OUTCOME_REJECTED);
    assert(ecobin_uart_read_u16_be(view.payload + DECISION(ERROR_CODE))
        == ECOBIN_UART_NACK_ERROR_STATE_CONFLICT);
    assert(!preparation.baseline_active && !preparation.baseline_published
        && !preparation.baseline_begin_failed);
    assert(!preparation.weight.present && !preparation.weight.in_flight);
    assert(!endpoint.process_event.held);
}

static void scale_frame(int32_t grams, uint8_t *frame) {
    uint32_t raw = (uint32_t)grams;
    uint16_t crc;
    frame[0] = 1u; frame[1] = 3u; frame[2] = 4u;
    frame[3] = (uint8_t)(raw >> 8); frame[4] = (uint8_t)raw;
    frame[5] = (uint8_t)(raw >> 24); frame[6] = (uint8_t)(raw >> 16);
    crc = ScaleReader_Crc16(frame, 7u);
    frame[7] = (uint8_t)crc; frame[8] = (uint8_t)(crc >> 8);
}

static void advance_to(uint32_t target) {
    uint32_t now = RuntimeClock_Now();
    assert(target >= now);
    RuntimeClock_Advance(target - now);
}

static void sample_at(uint32_t request_at, int32_t grams, uint8_t corrupt) {
    uint8_t frame[9];
    uint32_t attempt, measurement = preparation.weight.measurement.result.measurement_id;
    advance_to(request_at);
    attempt = McuWeightRun_StartOwnedAttempt(&preparation.weight, request_at);
    assert(attempt != 0u);
    scale_frame(grams, frame);
    if (corrupt) frame[7] ^= 1u;
    advance_to(request_at + 20u);
    assert(McuWeightRun_FinishOwnedAttempt(&preparation.weight, measurement,
        attempt, request_at + 20u, request_at + 20u, frame, sizeof(frame)));
    assert(McuWorkPreparation_Poll(&preparation, &endpoint, request_at + 20u));
}

static const uint8_t *held_result(void) {
    uint8_t query[ECOBIN_UART_QUERY_PROCESS_EVENT_PAYLOAD_MAX_LENGTH];
    ecobin_uart_frame_view_t event;
    memset(query, 0, sizeof(query));
    ecobin_uart_write_u64_be(query + ECOBIN_UART_QUERY_PROCESS_EVENT_QUERY_ID_OFFSET, 9u);
    memcpy(query + ECOBIN_UART_QUERY_PROCESS_EVENT_MCU_COMMAND_UID_OFFSET,
        preparation.baseline_scope, sizeof(preparation.baseline_scope));
    feed(ECOBIN_UART_MESSAGE_QUERY_PROCESS_EVENT, query, sizeof(query), RuntimeClock_Now64Locked());
    assert(reply_count == 2u);
    assert(reply(0u).message_type == ECOBIN_UART_MESSAGE_PROCESS_EVENT_QUERY_REPLY);
    assert(reply(0u).payload[ECOBIN_UART_PROCESS_EVENT_QUERY_REPLY_STATUS_OFFSET]
        == ECOBIN_UART_RESULT_QUERY_STATUS_HELD);
    event = reply(1u);
    assert(event.message_type == ECOBIN_UART_MESSAGE_BASELINE_MEASUREMENT_RESULT);
    return event.payload;
}

static void save_held_result(void) {
    feed(ECOBIN_UART_MESSAGE_PROCESS_EVENT_SAVED, endpoint.process_event.identity,
        ECOBIN_UART_PROCESS_EVENT_SAVED_PAYLOAD_MAX_LENGTH, RuntimeClock_Now64Locked());
    assert(reply_count == 1u);
    assert(reply(0u).message_type == ECOBIN_UART_MESSAGE_PROCESS_EVENT_SAVED_REPLY);
    assert(reply(0u).payload[ECOBIN_UART_PROCESS_EVENT_SAVED_REPLY_STATUS_OFFSET]
        == ECOBIN_UART_RESULT_SAVED_STATUS_RELEASED);
    assert(McuWorkPreparation_Poll(&preparation, &endpoint, RuntimeClock_Now64Locked()));
    assert(!preparation.baseline_active);
}

static void test_dispatch_stable_result_duplicate_and_saved_retention(void) {
    uint8_t command[ECOBIN_UART_MEASURE_BASELINE_PAYLOAD_MAX_LENGTH];
    uint8_t duplicate_result[ECOBIN_UART_BASELINE_MEASUREMENT_RESULT_PAYLOAD_MAX_LENGTH];
    const uint8_t *event;
    uint32_t i, measurement;
    static const int32_t samples[5] = {450, 500, 550, 510, 490};
    reset_runtime(42u);
    make_command(command, 42u, 1u, 0x11u, 0x22u);
    accept(command, 0u);
    measurement = preparation.weight.measurement.result.measurement_id;
    for (i = 0u; i < 5u; ++i) sample_at(i * 250u, samples[i], 0u);
    event = held_result();
    assert(event[RESULT(MEASUREMENT_KIND)] == ECOBIN_UART_RESULT_MEASUREMENT_KIND_STABLE_MEAN);
    assert((int32_t)ecobin_uart_read_u32_be(event + RESULT(REPORTED_WEIGHT_GRAMS)) == 500);
    assert(event[RESULT(SAMPLE_COUNT)] == 5u);
    assert(ecobin_uart_read_u32_be(event + RESULT(SAMPLE_SPAN_GRAMS)) == 100u);
    assert(memcmp(event + RESULT(MEASUREMENT_UID), command + BASE(MEASUREMENT_UID), 16u) == 0);
    memcpy(duplicate_result, event, sizeof(duplicate_result));
    accept(command, RuntimeClock_Now64Locked());
    assert(preparation.weight.measurement.result.measurement_id == measurement);
    assert(preparation.baseline_active && preparation.baseline_published && endpoint.process_event.held);
    assert(memcmp(endpoint.process_event.payload, duplicate_result, sizeof(duplicate_result)) == 0);
    save_held_result();
    make_command(command, 42u, 2u, 0x23u, 0x24u);
    accept(command, RuntimeClock_Now64Locked());
    assert(preparation.baseline_active && !preparation.baseline_published);
    assert(preparation.weight.measurement.result.measurement_id == measurement + 1u);
}

static void test_timeout_median_uses_all_actual_samples(void) {
    uint8_t command[ECOBIN_UART_MEASURE_BASELINE_PAYLOAD_MAX_LENGTH];
    const uint8_t *event;
    uint32_t i;
    reset_runtime(42u);
    make_command(command, 42u, 1u, 0x31u, 0x32u);
    accept(command, 0u);
    for (i = 0u; i < 20u; ++i) sample_at(i * 250u, (i & 1u) ? 1000 : -1000, 0u);
    advance_to(5000u);
    assert(McuWorkPreparation_Poll(&preparation, &endpoint, 5000u));
    event = held_result();
    assert(event[RESULT(MEASUREMENT_KIND)] == ECOBIN_UART_RESULT_MEASUREMENT_KIND_TIMEOUT_MEDIAN);
    assert((int32_t)ecobin_uart_read_u32_be(event + RESULT(REPORTED_WEIGHT_GRAMS)) == 0);
    assert(ecobin_uart_read_u16_be(event + RESULT(MEASUREMENT_ELAPSED_MS)) == 5000u);
    assert(event[RESULT(SAMPLE_COUNT)] == 20u);
    assert(ecobin_uart_read_u32_be(event + RESULT(SAMPLE_SPAN_GRAMS)) == 2000u);
}

static void test_response_timeout_and_sensor_protocol_fault_are_distinct(void) {
    uint8_t command[ECOBIN_UART_MEASURE_BASELINE_PAYLOAD_MAX_LENGTH];
    const uint8_t *event;
    uint32_t i, attempt, measurement;
    reset_runtime(42u);
    make_command(command, 42u, 1u, 0x41u, 0x42u);
    accept(command, 0u);
    measurement = preparation.weight.measurement.result.measurement_id;
    for (i = 0u; i < 20u; ++i) {
        advance_to(i * 250u);
        attempt = McuWeightRun_StartOwnedAttempt(&preparation.weight, i * 250u);
        assert(attempt != 0u);
        advance_to(i * 250u + 200u);
        assert(McuWeightRun_Poll(&preparation.weight, i * 250u + 200u));
        assert(McuWorkPreparation_Poll(&preparation, &endpoint, i * 250u + 200u));
        assert(preparation.weight.measurement.result.measurement_id == measurement);
    }
    advance_to(5000u);
    assert(McuWorkPreparation_Poll(&preparation, &endpoint, 5000u));
    event = held_result();
    assert(event[RESULT(MEASUREMENT_KIND)] == ECOBIN_UART_RESULT_MEASUREMENT_KIND_DISCONNECTED);
    assert(ecobin_uart_read_u16_be(event + RESULT(FAULT_CODE)) == ECOBIN_UART_FAULT_CODE_WEIGHT_DISCONNECTED);
    assert(event[RESULT(SAMPLE_COUNT)] == 0u);

    reset_runtime(42u);
    make_command(command, 42u, 1u, 0x51u, 0x52u);
    accept(command, 0u);
    for (i = 0u; i < 20u; ++i) sample_at(i * 250u, 500, 1u);
    advance_to(5000u);
    assert(McuWorkPreparation_Poll(&preparation, &endpoint, 5000u));
    event = held_result();
    assert(event[RESULT(MEASUREMENT_KIND)] == ECOBIN_UART_RESULT_MEASUREMENT_KIND_PROTOCOL_ERROR);
    assert(ecobin_uart_read_u16_be(event + RESULT(FAULT_CODE)) == ECOBIN_UART_FAULT_CODE_WEIGHT_PROTOCOL);
    assert(event[RESULT(SAMPLE_COUNT)] == 0u);
}

static void test_config_port_busy_guards_and_reset_forget_old_result(void) {
    uint8_t command[ECOBIN_UART_MEASURE_BASELINE_PAYLOAD_MAX_LENGTH];
    ecobin_uart_frame_view_t view;
    reset_runtime(42u);
    make_command(command, 42u, 1u, 0x61u, 0x62u);
    command[BASE(PORT_NO)] = 2u;
    command_digest(command);
    feed(ECOBIN_UART_MESSAGE_MEASURE_BASELINE, command, sizeof(command), 0u);
    view = reply(0u);
    assert(view.payload[DECISION(OUTCOME)] == ECOBIN_UART_COMMAND_OUTCOME_REJECTED);
    assert(ecobin_uart_read_u16_be(view.payload + DECISION(ERROR_CODE)) == ECOBIN_UART_NACK_ERROR_STATE_CONFLICT);

    make_command(command, 42u, 2u, 0x63u, 0x64u);
    command[BASE(CONFIG_CONTENT_SHA256)] ^= 1u;
    command_digest(command);
    feed(ECOBIN_UART_MESSAGE_MEASURE_BASELINE, command, sizeof(command), 0u);
    view = reply(0u);
    assert(view.payload[DECISION(OUTCOME)] == ECOBIN_UART_COMMAND_OUTCOME_REJECTED);
    assert(ecobin_uart_read_u16_be(view.payload + DECISION(ERROR_CODE)) == ECOBIN_UART_NACK_ERROR_STATE_CONFLICT);

    assert(McuDeviceFacts_PublishConfiguration(&endpoint.facts, 8u,
        content_sha256, mcu_sha256, 1u));
    make_command(command, 42u, 3u, 0x65u, 0x66u);
    feed(ECOBIN_UART_MESSAGE_MEASURE_BASELINE, command, sizeof(command), 0u);
    view = reply(0u);
    assert(view.payload[DECISION(OUTCOME)] == ECOBIN_UART_COMMAND_OUTCOME_REJECTED);
    assert(ecobin_uart_read_u16_be(view.payload + DECISION(ERROR_CODE)) == ECOBIN_UART_NACK_ERROR_STATE_CONFLICT);
    assert(McuDeviceFacts_PublishConfiguration(&endpoint.facts, 8u,
        content_sha256, mcu_sha256, 0u));

    make_command(command, 42u, 4u, 0x67u, 0x68u);
    accept(command, 0u);
    make_command(command, 42u, 5u, 0x69u, 0x6au);
    feed(ECOBIN_UART_MESSAGE_MEASURE_BASELINE, command, sizeof(command), 0u);
    view = reply(0u);
    assert(view.payload[DECISION(OUTCOME)] == ECOBIN_UART_COMMAND_OUTCOME_REJECTED);
    assert(ecobin_uart_read_u16_be(view.payload + DECISION(ERROR_CODE)) == ECOBIN_UART_NACK_ERROR_BUSY);

    /* Actual MCU reset creates a new boot and cannot manufacture or retain the
     * old boot's pending measurement/event. A Pi reconnect never calls Init. */
    reset_runtime(43u);
    assert(!preparation.baseline_active && !preparation.baseline_published);
    assert(!endpoint.process_event.held && endpoint.process_event.highest_sequence == 0u);
    assert(!preparation.weight.present);
}

static void test_corrupt_weight_policy_is_rejected_before_acceptance(void) {
    uint8_t command[ECOBIN_UART_MEASURE_BASELINE_PAYLOAD_MAX_LENGTH];

    /* The typed policy is present but cannot start the five-second sampler. */
    reset_runtime(42u);
    ecobin_uart_write_u16_be(preparation.configuration.active.preimage
        + PORT(WEIGHT_REQUIRED_SAMPLE_COUNT), 0u);
    make_command(command, 42u, 1u, 0x71u, 0x72u);
    reject_without_baseline(command, 0u);

    /* Device-level polling constraints are Begin preconditions too. */
    reset_runtime(42u);
    ecobin_uart_write_u32_be(preparation.configuration.active.preimage
        + DEVICE(WEIGHT_POLL_INTERVAL_MS), 5001u);
    make_command(command, 42u, 1u, 0x73u, 0x74u);
    reject_without_baseline(command, 0u);

    reset_runtime(42u);
    ecobin_uart_write_u32_be(preparation.configuration.active.preimage
        + DEVICE(WEIGHT_RESPONSE_TIMEOUT_MS), 251u);
    make_command(command, 42u, 1u, 0x75u, 0x76u);
    reject_without_baseline(command, 0u);

    /* Reading port one must not hide a corrupted port identity in that block. */
    reset_runtime(42u);
    preparation.configuration.active.preimage[PORT(PORT_NO)] = 0u;
    make_command(command, 42u, 1u, 0x77u, 0x78u);
    reject_without_baseline(command, 0u);
}

int main(void) {
    test_dispatch_stable_result_duplicate_and_saved_retention();
    test_timeout_median_uses_all_actual_samples();
    test_response_timeout_and_sensor_protocol_fault_are_distinct();
    test_config_port_busy_guards_and_reset_forget_old_result();
    test_corrupt_weight_policy_is_rejected_before_acceptance();
    return 0;
}
