#ifndef ECOBIN_TRANSPORT_H
#define ECOBIN_TRANSPORT_H

#include <stdint.h>

#include "uar/ecobin_uart_protocol.h"

typedef enum {
    ECOBIN_TRANSPORT_UNINIT = 0,
    ECOBIN_TRANSPORT_HELLO_SENT = 1,
    ECOBIN_TRANSPORT_READY = 2
} ecobin_transport_state_t;

typedef struct {
    uint8_t type;
    const uint8_t *payload;
    uint16_t payload_length;
    uint32_t tx_sequence;
} ecobin_transport_rx_msg_t;

void ecobin_transport_init(
    const char *firmware_identity,
    const char *firmware_version);
void ecobin_transport_feed_byte(uint8_t byte);
void ecobin_transport_tick_100ms(void);
int ecobin_transport_poll(ecobin_transport_rx_msg_t *rx_msg);
ecobin_transport_state_t ecobin_transport_get_state(void);

void ecobin_transport_send_door_state(
    ecobin_uart_delivery_door_state_t state,
    ecobin_uart_delivery_door_health_t health);
void ecobin_transport_send_safety_sensor_event(
    ecobin_uart_smoke_state_t smoke_state,
    ecobin_uart_sensor_health_t sensor_health,
    uint16_t fault_code);
void ecobin_transport_send_postclose_weight(
    int32_t stable_weight_grams,
    int32_t last_observed_weight_grams,
    ecobin_uart_measurement_status_t status,
    ecobin_uart_sensor_health_t sensor_health,
    uint16_t fault_code);
void ecobin_transport_send_fullness_result(
    ecobin_uart_infrared_value_t infrared_value,
    ecobin_uart_sensor_health_t infrared_health,
    int32_t stable_weight_grams,
    ecobin_uart_measurement_status_t measurement_status,
    ecobin_uart_sensor_health_t weight_sensor_health,
    uint16_t fault_code);
void ecobin_transport_send_fault(
    ecobin_uart_fault_component_t component,
    ecobin_uart_fault_severity_t severity,
    ecobin_uart_fault_lifecycle_t lifecycle,
    uint16_t fault_code);

#if defined(ECOBIN_TRANSPORT_HOST_TEST)
void ecobin_transport_host_send(const uint8_t *data, uint16_t length);
#endif

#endif
