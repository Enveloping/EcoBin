#ifndef ECOBIN_MCU_DEVICE_ENTRY_URL_H
#define ECOBIN_MCU_DEVICE_ENTRY_URL_H

#include "mcu_work_state.h"
#include "mcu_session.h"

#define MCU_DEVICE_ENTRY_URL_MAX_LENGTH 192u

typedef struct McuControlEndpoint McuControlEndpoint;
typedef uint8_t (*McuDeviceEntryUrlWriter)(const uint8_t *url,
    uint16_t length, void *context);

/* Boot-local, foreground-only URL transaction. The writer's successful return
 * means one complete HMI instruction entered its TX queue. No Flash, GPIO,
 * USART completion wait or HMI acknowledgement is owned here. */
typedef struct {
    uint8_t staging_url[MCU_DEVICE_ENTRY_URL_MAX_LENGTH];
    uint8_t active_url[MCU_DEVICE_ENTRY_URL_MAX_LENGTH];
    uint8_t staging_application_uid[16];
    uint8_t staging_sha256[32];
    uint8_t active_application_uid[16];
    uint8_t active_sha256[32];
    uint8_t result[ECOBIN_UART_DEVICE_ENTRY_URL_APPLY_RESULT_PAYLOAD_MAX_LENGTH];
    McuSessionCommand result_command;
    McuDeviceEntryUrlWriter writer;
    void *writer_context;
    uint64_t boot_id;
    uint16_t staging_length;
    uint16_t staging_received;
    uint16_t active_length;
    uint8_t staging_part_count;
    uint8_t staging_next_part;
    uint8_t staging_present;
    uint8_t active_present;
    uint8_t result_present;
} McuDeviceEntryUrl;

void McuDeviceEntryUrl_Init(McuDeviceEntryUrl *state,
    McuDeviceEntryUrlWriter writer, void *context);
void McuDeviceEntryUrl_Bind(McuDeviceEntryUrl *state, uint64_t boot_id);
uint8_t McuDeviceEntryUrl_Receive(McuDeviceEntryUrl *state,
    McuControlEndpoint *endpoint, uint16_t owner_error, uint8_t message,
    const uint8_t *payload, size_t length, uint64_t now_ms,
    McuSessionDecision *decision);
/* Exact cached COMMIT result only. Queries never call the HMI writer. */
size_t McuDeviceEntryUrl_CopyResult(const McuDeviceEntryUrl *state,
    const McuSessionCommand *command, uint8_t *message,
    uint8_t *output, size_t capacity);
uint8_t McuDeviceEntryUrl_IsStaging(const McuDeviceEntryUrl *state);

#endif
