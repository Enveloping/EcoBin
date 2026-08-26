#include "factory_sim.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define TX_CAPACITY 4096u

typedef struct Capture {
    uint8_t bytes[TX_CAPACITY];
    size_t length;
} Capture;

static int g_failures;

#define CHECK(condition)                                                        \
    do {                                                                        \
        if (!(condition)) {                                                      \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition); \
            ++g_failures;                                                        \
        }                                                                       \
    } while (0)

static void capture_byte(uint8_t value, void *user)
{
    Capture *capture = (Capture *)user;

    if (capture->length < TX_CAPACITY) {
        capture->bytes[capture->length++] = value;
    }
}

static void reset_capture(Capture *capture)
{
    capture->length = 0u;
}

static void feed(FactorySim *sim, const uint8_t *data, size_t length,
                 uint32_t now_ms)
{
    factory_sim_feed(sim, data, length, now_ms);
}

static uint32_t weight_from_f1(const uint8_t *frame)
{
    return ((uint32_t)frame[2] << 16) |
           ((uint32_t)frame[3] << 8) |
           (uint32_t)frame[4];
}

static void test_identity_and_weight_script(void)
{
    static const uint8_t f2_query[] = {0xF2u, 0x01u, 0xF2u};
    static const uint8_t f0_query[] = {0xF0u, 0x01u, 0xF0u};
    static const uint32_t expected_weights[] = {
        0u, 0u, 0u, 0u, 500u, 500u, 500u, 0u, 0u
    };
    static const char version[] = "factory-sim-1.0.0";
    static const char identity[] = "ECOSIM01";
    FactorySim sim;
    Capture capture = {{0}, 0u};
    size_t i;

    factory_sim_init(&sim, capture_byte, &capture);
    feed(&sim, f2_query, sizeof(f2_query), 0u);
    CHECK(capture.length == 51u);
    CHECK(capture.bytes[0] == 0xF3u && capture.bytes[50] == 0xF3u);
    CHECK(capture.bytes[1] == 0x01u && capture.bytes[2] == 0x00u);
    CHECK(capture.bytes[3] == 0x02u);
    CHECK(capture.bytes[4] == 0u && capture.bytes[5] == 0u &&
          capture.bytes[6] == 0u && capture.bytes[7] == 1u);
    CHECK(capture.bytes[8] == sizeof(version) - 1u);
    CHECK(memcmp(&capture.bytes[9], version, sizeof(version) - 1u) == 0);
    for (i = 9u + sizeof(version) - 1u; i <= 40u; ++i) {
        CHECK(capture.bytes[i] == 0u);
    }
    CHECK(memcmp(&capture.bytes[41], identity, 8u) == 0);
    CHECK(capture.bytes[49] == 0x0Fu);

    for (i = 0u; i < sizeof(expected_weights) / sizeof(expected_weights[0]); ++i) {
        reset_capture(&capture);
        feed(&sim, f0_query, sizeof(f0_query), (uint32_t)i);
        CHECK(capture.length == 8u);
        CHECK(capture.bytes[0] == 0xF1u && capture.bytes[7] == 0xF1u);
        CHECK(capture.bytes[1] == 0x03u);
        CHECK(weight_from_f1(capture.bytes) == expected_weights[i]);
        CHECK(capture.bytes[5] == 0u && capture.bytes[6] == 0u);
    }
    for (i = 0u; i < 260u; ++i) {
        reset_capture(&capture);
        feed(&sim, f0_query, sizeof(f0_query), (uint32_t)(100u + i));
        CHECK(weight_from_f1(capture.bytes) == 0u);
    }

    reset_capture(&capture);
    feed(&sim, f2_query, sizeof(f2_query), 20u);
    reset_capture(&capture);
    feed(&sim, f0_query, sizeof(f0_query), 21u);
    CHECK(weight_from_f1(capture.bytes) == 0u);
}

static void test_split_coalesced_noise_and_resync(void)
{
    static const uint8_t part1[] = {0x00u, 0x7Fu, 0xF2u};
    static const uint8_t part2[] = {
        0x01u, 0xF2u,
        0xAAu, 0x01u, 0x00u,
        0xF0u, 0x01u, 0xF0u
    };
    FactorySim sim;
    Capture capture = {{0}, 0u};
    uint8_t noise[600];
    size_t i;

    factory_sim_init(&sim, capture_byte, &capture);
    feed(&sim, part1, sizeof(part1), 0u);
    CHECK(capture.length == 0u);
    feed(&sim, part2, sizeof(part2), 0u);
    CHECK(capture.length == 59u);
    CHECK(capture.bytes[0] == 0xF3u);
    CHECK(capture.bytes[51] == 0xF1u);

    for (i = 0u; i < sizeof(noise); ++i) {
        noise[i] = 0x55u;
    }
    feed(&sim, noise, sizeof(noise), 1u);
    CHECK(sim.rx_length < FACTORY_SIM_RX_CAPACITY);
    reset_capture(&capture);
    {
        static const uint8_t query[] = {0xF2u, 0x01u, 0xF2u};
        feed(&sim, query, sizeof(query), 2u);
    }
    CHECK(capture.length == 51u);

    reset_capture(&capture);
    {
        static const uint8_t stalled_then_query[] = {
            0xA0u, 0xF2u, 0x01u, 0xF2u
        };
        feed(&sim, stalled_then_query, sizeof(stalled_then_query), 10u);
        CHECK(capture.length == 0u);
        factory_sim_poll(&sim, 110u);
        CHECK(capture.length == 51u);
    }
}

static void test_candidate_timeout_is_not_extended_by_trickle_bytes(void)
{
    static const uint8_t stalled_a0[] = {0xA0u};
    static const uint8_t trickle_noise[] = {0x55u};
    static const uint8_t f2_query[] = {0xF2u, 0x01u, 0xF2u};
    FactorySim sim;
    Capture capture = {{0}, 0u};

    factory_sim_init(&sim, capture_byte, &capture);

    /*
     * The A0 candidate starts at t=0.  A later noise byte must not extend its
     * 100 ms completion deadline.  At t=180 the stale A0 is discarded before
     * the following F2 query is parsed.
     */
    feed(&sim, stalled_a0, sizeof(stalled_a0), 0u);
    feed(&sim, trickle_noise, sizeof(trickle_noise), 90u);
    feed(&sim, f2_query, sizeof(f2_query), 180u);

    CHECK(capture.length == 51u);
    CHECK(capture.bytes[0] == 0xF3u && capture.bytes[50] == 0xF3u);
}

static void test_invalid_fields_resynchronize_one_byte(void)
{
    static const uint8_t invalid_then_valid_f0[] = {
        0xF0u, 0x02u, 0xF0u, 0x01u, 0xF0u
    };
    static const uint8_t invalid_then_valid_f2[] = {
        0xF2u, 0x03u, 0xF2u, 0x01u, 0xF2u
    };
    static const uint8_t invalid_then_valid_aa[] = {
        0xAAu, 0x02u, 0xAAu, 0x01u, 0xAAu
    };
    static const uint8_t invalid_then_valid_bb[] = {
        0xBBu, 0x0Au, 0xBBu, 0x04u, 0xBBu
    };
    static const uint8_t invalid_then_valid_ee[] = {
        0xEEu, 0x02u, 0xEEu, 0x01u, 0xEEu
    };
    FactorySim sim;
    Capture capture = {{0}, 0u};
    uint8_t invalid_a0[195] = {0};

    factory_sim_init(&sim, capture_byte, &capture);
    feed(&sim, invalid_then_valid_f0, sizeof(invalid_then_valid_f0), 0u);
    CHECK(capture.length == 8u);
    CHECK(capture.bytes[0] == 0xF1u && capture.bytes[7] == 0xF1u);

    factory_sim_init(&sim, capture_byte, &capture);
    reset_capture(&capture);
    feed(&sim, invalid_then_valid_f2, sizeof(invalid_then_valid_f2), 1u);
    CHECK(capture.length == 51u);
    CHECK(capture.bytes[0] == 0xF3u && capture.bytes[50] == 0xF3u);

    factory_sim_init(&sim, capture_byte, &capture);
    reset_capture(&capture);
    feed(&sim, invalid_then_valid_aa, sizeof(invalid_then_valid_aa), 2u);
    CHECK(sim.action == FACTORY_SIM_ACTION_DELIVERY);

    factory_sim_init(&sim, capture_byte, &capture);
    reset_capture(&capture);
    feed(&sim, invalid_then_valid_bb, sizeof(invalid_then_valid_bb), 3u);
    CHECK(sim.unit_price == 4u);

    factory_sim_init(&sim, capture_byte, &capture);
    reset_capture(&capture);
    feed(&sim, invalid_then_valid_ee, sizeof(invalid_then_valid_ee), 4u);
    CHECK(sim.action == FACTORY_SIM_ACTION_CLEAN);

    /* An invalid full A0 candidate must not swallow a valid F2 in its body. */
    factory_sim_init(&sim, capture_byte, &capture);
    reset_capture(&capture);
    invalid_a0[0] = 0xA0u;
    invalid_a0[1] = 0x00u; /* invalid LEN */
    invalid_a0[2] = 0xF2u;
    invalid_a0[3] = 0x01u;
    invalid_a0[4] = 0xF2u;
    invalid_a0[194] = 0xA0u;
    feed(&sim, invalid_a0, sizeof(invalid_a0), 5u);
    CHECK(capture.length == 51u);
    CHECK(capture.bytes[0] == 0xF3u && capture.bytes[50] == 0xF3u);
}

static void make_url_frame(uint8_t *frame, const char *url)
{
    const size_t length = strlen(url);
    size_t i;

    memset(frame, 0, 195u);
    frame[0] = 0xA0u;
    frame[1] = (uint8_t)length;
    for (i = 0u; i < length; ++i) {
        frame[2u + i] = (uint8_t)url[i];
    }
    frame[194] = 0xA0u;
}

static void test_url_validation(void)
{
    static const char url[] = "https://example.test/device/ECOSIM01";
    FactorySim sim;
    Capture capture = {{0}, 0u};
    uint8_t frame[195];

    factory_sim_init(&sim, capture_byte, &capture);
    make_url_frame(frame, url);
    feed(&sim, frame, 17u, 0u);
    feed(&sim, &frame[17], sizeof(frame) - 17u, 1u);
    CHECK(sim.url_length == strlen(url));
    CHECK(memcmp(sim.url, url, strlen(url)) == 0);
    CHECK(capture.length == 0u);

    {
        char max_url[193];
        size_t i;
        memcpy(max_url, "https://", 8u);
        for (i = 8u; i < 192u; ++i) {
            max_url[i] = 'a';
        }
        max_url[192] = '\0';
        make_url_frame(frame, max_url);
        feed(&sim, frame, sizeof(frame), 2u);
        CHECK(sim.url_length == 192u);
        CHECK(memcmp(sim.url, max_url, 192u) == 0);
    }
    make_url_frame(frame, url);
    feed(&sim, frame, sizeof(frame), 2u);

    make_url_frame(frame, "http://not-https.test/");
    feed(&sim, frame, sizeof(frame), 2u);
    CHECK(sim.url_length == strlen(url));
    CHECK(memcmp(sim.url, url, strlen(url)) == 0);

    make_url_frame(frame, "https://replacement.test/");
    frame[193] = 0x7Fu;
    feed(&sim, frame, sizeof(frame), 3u);
    CHECK(sim.url_length == strlen(url));

    make_url_frame(frame, "https://tail-resync.test/");
    frame[194] = 0xF0u;
    feed(&sim, frame, sizeof(frame), 4u);
    {
        static const uint8_t rest[] = {0x01u, 0xF0u};
        feed(&sim, rest, sizeof(rest), 4u);
    }
    CHECK(capture.length == 8u);
}

static void test_delivery_clean_and_update_latch(void)
{
    static const uint8_t delivery_commands[] = {
        0xBBu, 0x04u, 0xBBu, 0xAAu, 0x01u, 0xAAu
    };
    static const uint8_t clean[] = {0xEEu, 0x01u, 0xEEu};
    static const uint8_t prepare[] = {0xF2u, 0x02u, 0xF2u};
    static const uint8_t f0[] = {0xF0u, 0x01u, 0xF0u};
    static const uint8_t expected_dd[] = {
        0xDDu, 0x00u, 0x00u, 0x00u, 0x00u, 0x01u, 0xF4u, 0x00u, 0xDDu
    };
    static const uint8_t expected_ef[] = {
        0xEFu, 0x00u, 0x01u, 0xF4u, 0x00u, 0x00u, 0x00u, 0x00u, 0xEFu
    };
    FactorySim sim;
    Capture capture = {{0}, 0u};
    uint8_t url_frame[195];

    factory_sim_init(&sim, capture_byte, &capture);
    feed(&sim, delivery_commands, sizeof(delivery_commands), 100u);
    CHECK(sim.unit_price == 4u);
    CHECK(sim.action == FACTORY_SIM_ACTION_DELIVERY);
    feed(&sim, clean, sizeof(clean), 200u);
    {
        static const uint8_t repeated_delivery[] = {0xAAu, 0x01u, 0xAAu};
        feed(&sim, repeated_delivery, sizeof(repeated_delivery), 200u);
    }
    CHECK(sim.action == FACTORY_SIM_ACTION_DELIVERY);
    factory_sim_poll(&sim, 1099u);
    CHECK(capture.length == 0u);
    factory_sim_poll(&sim, 1100u);
    CHECK(capture.length == sizeof(expected_dd));
    CHECK(memcmp(capture.bytes, expected_dd, sizeof(expected_dd)) == 0);
    factory_sim_poll(&sim, 5000u);
    CHECK(capture.length == sizeof(expected_dd));

    reset_capture(&capture);
    feed(&sim, clean, sizeof(clean), 6000u);
    factory_sim_poll(&sim, 7000u);
    CHECK(capture.length == sizeof(expected_ef));
    CHECK(memcmp(capture.bytes, expected_ef, sizeof(expected_ef)) == 0);

    reset_capture(&capture);
    feed(&sim, delivery_commands, sizeof(delivery_commands), 8000u);
    CHECK(sim.action == FACTORY_SIM_ACTION_DELIVERY);
    feed(&sim, prepare, sizeof(prepare), 8100u);
    CHECK(sim.update_latched == 1u);
    CHECK(sim.action == FACTORY_SIM_ACTION_IDLE);
    CHECK(capture.length == 51u);
    CHECK(capture.bytes[1] == 0x02u && capture.bytes[2] == 0x00u);
    CHECK(capture.bytes[49] == 0x1Fu);
    factory_sim_poll(&sim, 9000u);
    CHECK(capture.length == 51u);

    make_url_frame(url_frame, "https://ignored-after-prepare.test/");
    {
        static const uint8_t ignored_commands[] = {
            0xBBu, 0x09u, 0xBBu, 0xAAu, 0x01u, 0xAAu
        };
        feed(&sim, ignored_commands, sizeof(ignored_commands), 9000u);
    }
    feed(&sim, clean, sizeof(clean), 9000u);
    feed(&sim, url_frame, sizeof(url_frame), 9000u);
    CHECK(sim.rx_length == 0u);
    CHECK(capture.length == 51u);
    CHECK(sim.action == FACTORY_SIM_ACTION_IDLE);
    CHECK(sim.url_length == 0u);
    CHECK(sim.unit_price == 4u);

    reset_capture(&capture);
    feed(&sim, f0, sizeof(f0), 9001u);
    CHECK(capture.length == 8u);
    reset_capture(&capture);
    feed(&sim, prepare, sizeof(prepare), 9002u);
    CHECK(capture.length == 51u && capture.bytes[49] == 0x1Fu);
}

static void test_timer_wraparound(void)
{
    static const uint8_t delivery[] = {0xAAu, 0x01u, 0xAAu};
    static const uint8_t stalled_a0[] = {0xA0u};
    static const uint8_t trickle_noise[] = {0x55u};
    static const uint8_t f2_query[] = {0xF2u, 0x01u, 0xF2u};
    FactorySim sim;
    Capture capture = {{0}, 0u};

    factory_sim_init(&sim, capture_byte, &capture);
    feed(&sim, delivery, sizeof(delivery), UINT32_MAX - 499u);
    factory_sim_poll(&sim, 499u);
    CHECK(capture.length == 0u);
    factory_sim_poll(&sim, 500u);
    CHECK(capture.length == 9u);

    factory_sim_init(&sim, capture_byte, &capture);
    reset_capture(&capture);
    feed(&sim, stalled_a0, sizeof(stalled_a0), UINT32_MAX - 49u);
    feed(&sim, trickle_noise, sizeof(trickle_noise), 40u);
    feed(&sim, f2_query, sizeof(f2_query), 51u);
    CHECK(capture.length == 51u);
    CHECK(capture.bytes[0] == 0xF3u && capture.bytes[50] == 0xF3u);
}

int main(void)
{
    test_identity_and_weight_script();
    test_split_coalesced_noise_and_resync();
    test_candidate_timeout_is_not_extended_by_trickle_bytes();
    test_invalid_fields_resynchronize_one_byte();
    test_url_validation();
    test_delivery_clean_and_update_latch();
    test_timer_wraparound();

    if (g_failures != 0) {
        fprintf(stderr, "%d factory simulation test(s) failed\n", g_failures);
        return 1;
    }
    printf("factory simulation tests passed\n");
    return 0;
}
