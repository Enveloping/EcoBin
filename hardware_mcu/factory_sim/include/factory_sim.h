#ifndef ECOBIN_FACTORY_SIM_H
#define ECOBIN_FACTORY_SIM_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FACTORY_SIM_RX_CAPACITY 256u
#define FACTORY_SIM_URL_CAPACITY 192u

typedef void (*FactorySimTxByte)(uint8_t value, void *user);

typedef enum FactorySimAction {
    FACTORY_SIM_ACTION_IDLE = 0,
    FACTORY_SIM_ACTION_DELIVERY = 1,
    FACTORY_SIM_ACTION_CLEAN = 2
} FactorySimAction;

/*
 * The structure is public so the bare-metal firmware can allocate it without
 * a heap.  Callers must treat every field except the documented observations
 * (unit_price, URL, action and update_latched) as implementation state.
 */
typedef struct FactorySim {
    FactorySimTxByte tx_byte;
    void *tx_user;
    uint8_t rx_buffer[FACTORY_SIM_RX_CAPACITY];
    uint16_t rx_length;
    uint8_t url[FACTORY_SIM_URL_CAPACITY];
    uint8_t url_length;
    uint8_t unit_price;
    uint8_t f0_query_count;
    uint8_t update_latched;
    uint8_t action;
    uint32_t action_deadline_ms;
    uint32_t rx_candidate_started_ms;
    uint8_t rx_candidate_time_valid;
} FactorySim;

void factory_sim_init(FactorySim *sim, FactorySimTxByte tx_byte, void *user);
void factory_sim_feed(FactorySim *sim, const uint8_t *data, size_t length,
                      uint32_t now_ms);
void factory_sim_poll(FactorySim *sim, uint32_t now_ms);

#ifdef __cplusplus
}
#endif

#endif
