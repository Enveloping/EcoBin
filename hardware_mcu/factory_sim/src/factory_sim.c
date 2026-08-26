#include "factory_sim.h"

#define FRAME_A0 0xA0u
#define FRAME_AA 0xAAu
#define FRAME_BB 0xBBu
#define FRAME_DD 0xDDu
#define FRAME_EE 0xEEu
#define FRAME_EF 0xEFu
#define FRAME_F0 0xF0u
#define FRAME_F1 0xF1u
#define FRAME_F2 0xF2u
#define FRAME_F3 0xF3u

#define SHORT_FRAME_LENGTH 3u
#define URL_FRAME_LENGTH 195u
#define ACTION_DELAY_MS 1000u
#define INCOMPLETE_FRAME_TIMEOUT_MS 100u

#define SENSOR_VALID_ALL 0x03u
#define SAFE_FLAGS_IDLE 0x0Fu
#define SAFE_FLAGS_UPDATE_LATCHED 0x1Fu

static const uint8_t k_version[] = "factory-sim-1.0.0";
static const uint8_t k_identity[8] = {
    'E', 'C', 'O', 'S', 'I', 'M', '0', '1'
};

static void send_byte(FactorySim *sim, uint8_t value)
{
    if (sim->tx_byte != (FactorySimTxByte)0) {
        sim->tx_byte(value, sim->tx_user);
    }
}

static void send_weight24(FactorySim *sim, uint32_t weight)
{
    send_byte(sim, (uint8_t)(weight >> 16));
    send_byte(sim, (uint8_t)(weight >> 8));
    send_byte(sim, (uint8_t)weight);
}

static void send_result(FactorySim *sim, uint8_t marker,
                        uint32_t before_weight, uint32_t after_weight)
{
    send_byte(sim, marker);
    send_weight24(sim, before_weight);
    send_weight24(sim, after_weight);
    send_byte(sim, 0x00u); /* simulated infrared: not blocked */
    send_byte(sim, marker);
}

static void send_self_test(FactorySim *sim)
{
    uint32_t weight;

    if (sim->f0_query_count < 8u) {
        sim->f0_query_count++;
    }
    if (sim->f0_query_count >= 5u && sim->f0_query_count <= 7u) {
        weight = 500u;
    } else {
        weight = 0u;
    }

    send_byte(sim, FRAME_F1);
    send_byte(sim, SENSOR_VALID_ALL);
    send_weight24(sim, weight);
    send_byte(sim, 0x00u); /* simulated infrared: not blocked */
    send_byte(sim, 0x00u); /* simulated smoke sensor: normal */
    send_byte(sim, FRAME_F1);
}

static void send_firmware_status(FactorySim *sim, uint8_t mode,
                                 uint8_t safe_flags)
{
    uint8_t i;
    const uint8_t version_length = (uint8_t)(sizeof(k_version) - 1u);

    send_byte(sim, FRAME_F3);
    send_byte(sim, mode);
    send_byte(sim, 0x00u); /* STATUS=OK */
    send_byte(sim, 0x02u); /* fixed-frame revision 2 */
    send_byte(sim, 0x00u);
    send_byte(sim, 0x00u);
    send_byte(sim, 0x00u);
    send_byte(sim, 0x01u); /* version code = 1 */
    send_byte(sim, version_length);
    for (i = 0u; i < 32u; ++i) {
        send_byte(sim, i < version_length ? k_version[i] : 0x00u);
    }
    for (i = 0u; i < 8u; ++i) {
        send_byte(sim, k_identity[i]);
    }
    send_byte(sim, safe_flags);
    send_byte(sim, FRAME_F3);
}

static uint8_t is_command_header(uint8_t value)
{
    return (uint8_t)(value == FRAME_A0 || value == FRAME_AA ||
                     value == FRAME_BB || value == FRAME_EE ||
                     value == FRAME_F0 || value == FRAME_F2);
}

static uint16_t expected_length(uint8_t header)
{
    return header == FRAME_A0 ? URL_FRAME_LENGTH : SHORT_FRAME_LENGTH;
}

static uint8_t valid_https_url(const uint8_t *frame)
{
    uint16_t i;
    const uint8_t length = frame[1];
    static const uint8_t prefix[] = "https://";

    if (length < (sizeof(prefix) - 1u) || length > FACTORY_SIM_URL_CAPACITY) {
        return 0u;
    }
    for (i = 0u; i < (sizeof(prefix) - 1u); ++i) {
        if (frame[2u + i] != prefix[i]) {
            return 0u;
        }
    }
    for (i = 0u; i < length; ++i) {
        if (frame[2u + i] < 0x21u || frame[2u + i] > 0x7Eu) {
            return 0u;
        }
    }
    for (i = length; i < FACTORY_SIM_URL_CAPACITY; ++i) {
        if (frame[2u + i] != 0x00u) {
            return 0u;
        }
    }
    return 1u;
}

static uint8_t valid_short_command(const uint8_t *frame)
{
    const uint8_t marker = frame[0];
    const uint8_t command = frame[1];

    if (marker == FRAME_AA || marker == FRAME_EE || marker == FRAME_F0) {
        return (uint8_t)(command == 0x01u);
    }
    if (marker == FRAME_BB) {
        return (uint8_t)(command <= 0x09u);
    }
    if (marker == FRAME_F2) {
        return (uint8_t)(command == 0x01u || command == 0x02u);
    }
    return 0u;
}

static void handle_url(FactorySim *sim, const uint8_t *frame)
{
    uint16_t i;

    if (sim->update_latched != 0u) {
        return;
    }
    sim->url_length = frame[1];
    for (i = 0u; i < sim->url_length; ++i) {
        sim->url[i] = frame[2u + i];
    }
    for (; i < FACTORY_SIM_URL_CAPACITY; ++i) {
        sim->url[i] = 0u;
    }
}

static void handle_short(FactorySim *sim, const uint8_t *frame,
                         uint32_t now_ms)
{
    const uint8_t marker = frame[0];
    const uint8_t command = frame[1];

    if (marker == FRAME_F2) {
        if (command == 0x01u) {
            sim->f0_query_count = 0u;
            send_firmware_status(
                sim, 0x01u,
                sim->update_latched != 0u
                    ? SAFE_FLAGS_UPDATE_LATCHED
                    : SAFE_FLAGS_IDLE);
        } else if (command == 0x02u) {
            sim->update_latched = 1u;
            sim->action = FACTORY_SIM_ACTION_IDLE;
            send_firmware_status(sim, 0x02u, SAFE_FLAGS_UPDATE_LATCHED);
        }
        return;
    }

    if (marker == FRAME_F0) {
        if (command == 0x01u) {
            send_self_test(sim);
        }
        return;
    }

    if (sim->update_latched != 0u) {
        return;
    }

    if (marker == FRAME_BB) {
        if (command <= 9u) {
            sim->unit_price = command;
        }
    } else if (marker == FRAME_AA) {
        if (command == 0x01u && sim->action == FACTORY_SIM_ACTION_IDLE) {
            sim->action = FACTORY_SIM_ACTION_DELIVERY;
            sim->action_deadline_ms = now_ms + ACTION_DELAY_MS;
        }
    } else if (marker == FRAME_EE) {
        if (command == 0x01u && sim->action == FACTORY_SIM_ACTION_IDLE) {
            sim->action = FACTORY_SIM_ACTION_CLEAN;
            sim->action_deadline_ms = now_ms + ACTION_DELAY_MS;
        }
    }
}

static void consume_prefix(FactorySim *sim, uint16_t count)
{
    uint16_t i;

    /* Removing the first byte completes, rejects or switches the candidate. */
    sim->rx_candidate_time_valid = 0u;
    if (count >= sim->rx_length) {
        sim->rx_length = 0u;
        return;
    }
    for (i = 0u; i < (uint16_t)(sim->rx_length - count); ++i) {
        sim->rx_buffer[i] = sim->rx_buffer[(uint16_t)(i + count)];
    }
    sim->rx_length = (uint16_t)(sim->rx_length - count);
}

static void process_rx(FactorySim *sim, uint32_t now_ms)
{
    uint16_t frame_length;
    uint16_t noise_length;

    for (;;) {
        noise_length = 0u;
        while (noise_length < sim->rx_length &&
               is_command_header(sim->rx_buffer[noise_length]) == 0u) {
            ++noise_length;
        }
        if (noise_length != 0u) {
            consume_prefix(sim, noise_length);
        }
        if (sim->rx_length == 0u) {
            return;
        }

        if (sim->rx_candidate_time_valid == 0u) {
            sim->rx_candidate_started_ms = now_ms;
            sim->rx_candidate_time_valid = 1u;
        } else if ((uint32_t)(now_ms - sim->rx_candidate_started_ms) >=
                   INCOMPLETE_FRAME_TIMEOUT_MS) {
            consume_prefix(sim, 1u);
            continue;
        }

        frame_length = expected_length(sim->rx_buffer[0]);
        if (sim->rx_length < frame_length) {
            return;
        }
        if (sim->rx_buffer[frame_length - 1u] != sim->rx_buffer[0]) {
            consume_prefix(sim, 1u);
            continue;
        }

        if (sim->rx_buffer[0] == FRAME_A0) {
            if (valid_https_url(sim->rx_buffer) == 0u) {
                consume_prefix(sim, 1u);
                continue;
            }
            handle_url(sim, sim->rx_buffer);
        } else {
            if (valid_short_command(sim->rx_buffer) == 0u) {
                consume_prefix(sim, 1u);
                continue;
            }
            handle_short(sim, sim->rx_buffer, now_ms);
        }
        consume_prefix(sim, frame_length);
    }
}

void factory_sim_init(FactorySim *sim, FactorySimTxByte tx_byte, void *user)
{
    uint16_t i;

    sim->tx_byte = tx_byte;
    sim->tx_user = user;
    sim->rx_length = 0u;
    sim->url_length = 0u;
    sim->unit_price = 0u;
    sim->f0_query_count = 0u;
    sim->update_latched = 0u;
    sim->action = FACTORY_SIM_ACTION_IDLE;
    sim->action_deadline_ms = 0u;
    sim->rx_candidate_started_ms = 0u;
    sim->rx_candidate_time_valid = 0u;
    for (i = 0u; i < FACTORY_SIM_URL_CAPACITY; ++i) {
        sim->url[i] = 0u;
    }
}

void factory_sim_feed(FactorySim *sim, const uint8_t *data, size_t length,
                      uint32_t now_ms)
{
    size_t i;

    for (i = 0u; i < length; ++i) {
        if (sim->rx_length >= FACTORY_SIM_RX_CAPACITY) {
            /* Keep the newest bytes and resume header scanning deterministically. */
            consume_prefix(sim, 1u);
        }
        sim->rx_buffer[sim->rx_length++] = data[i];
        process_rx(sim, now_ms);
    }
}

void factory_sim_poll(FactorySim *sim, uint32_t now_ms)
{
    process_rx(sim, now_ms);

    if (sim->action == FACTORY_SIM_ACTION_IDLE) {
        return;
    }
    if ((int32_t)(now_ms - sim->action_deadline_ms) < 0) {
        return;
    }

    if (sim->action == FACTORY_SIM_ACTION_DELIVERY) {
        send_result(sim, FRAME_DD, 0u, 500u);
    } else if (sim->action == FACTORY_SIM_ACTION_CLEAN) {
        send_result(sim, FRAME_EF, 500u, 0u);
    }
    sim->action = FACTORY_SIM_ACTION_IDLE;
}
