#ifndef ECOBIN_MCU_CONTROL_ENDPOINT_H
#define ECOBIN_MCU_CONTROL_ENDPOINT_H
#include "mcu_session.h"
#include "mcu_device_facts.h"
#include "mcu_process_event_slot.h"
#include "mcu_actuator_event_journal.h"

/* Native candidate, NOT connected to main/USART/Keil. Single foreground owner.
 * Init on actual MCU reset only, after actuator/clock hardware initialization.
 * Producers may update work/facts through their existing public APIs only
 * between Feed calls. Sink borrows bytes until return, must be bounded and
 * must not reenter Feed. No heap allocation, GPIO, Flash, automatic ACK or retry.
 */
typedef void (*McuControlSink)(const uint8_t *data, size_t length, void *context);
typedef struct McuControlEndpoint McuControlEndpoint;
/* Optional bounded foreground application. Called only after complete payload
 * validation. Return a real cached session decision, never a guessed ACK.
 * No reentry/attachment replacement; context outlives the endpoint. */
typedef uint8_t (*McuControlCommandHandler)(McuControlEndpoint *endpoint, uint8_t message,
    const uint8_t *payload, size_t length, uint64_t now_ms, void *context, McuSessionDecision *decision);
typedef void (*McuControlBoundHandler)(McuControlEndpoint *endpoint, void *context);
struct McuControlEndpoint {
    McuSession session;
    McuWorkState work;
    McuProcessEventSlot process_event;
    /* Endpoint-owned: attached producers MUST use endpoint actuator APIs, not
     * mutate this journal or critical_event_sequence through lower-level APIs. */
    McuActuatorEventJournal actuator_events;
    McuDeviceFacts facts;
    ecobin_uart_stream_parser_t parser;
    uint8_t payload[ECOBIN_UART_MAX_PAYLOAD_LENGTH];
    uint8_t transmit[ECOBIN_UART_MAX_FRAME_LENGTH];
    uint32_t tx_sequence;
    uint32_t critical_event_sequence;
    uint64_t last_input_ms;
    McuControlSink sink;
    void *sink_context;
    McuControlCommandHandler command_handler;
    McuControlBoundHandler bound_handler;
    void *application_context;
    uint8_t feeding;
};

void McuControlEndpoint_Init(McuControlEndpoint *endpoint, uint8_t port_no,
    McuControlSink sink, void *context);
/* Install once during boot initialization, before binding; null/late/replacement
 * attachment is rejected. Default endpoint remains read-only control handling. */
uint8_t McuControlEndpoint_AttachCommands(McuControlEndpoint *endpoint,
    McuControlCommandHandler commands, McuControlBoundHandler bound, void *context);
/* Shared boot-local critical event allocator for attached producers. Not the
 * transport sequence or measurement counter. 0 on unbound/exhausted; no wrap.
 * Cannot consume numbers promised to reserved actuator evidence. */
uint32_t McuControlEndpoint_ReserveEventSequence(McuControlEndpoint *endpoint);
/* Atomic RAM + future event-number credit reservation, without preassigning
 * numbers. Failure leaves state/output token unchanged. NOT motion permission. */
uint8_t McuControlEndpoint_ReserveActuatorEvents(McuControlEndpoint *endpoint,
    uint8_t count, McuActuatorEventReservation *reservation);
/* Caller must know motion has not started. Fails after any member publication,
 * even when that member was saved/reclaimed. Releases only unstarted resources. */
uint8_t McuControlEndpoint_CancelActuatorEvents(McuControlEndpoint *endpoint,
    const McuActuatorEventReservation *reservation);
/* Local evidence publication, NO output or UART transmission. Sequence must be
 * zero in the template; other fields final and current-boot scoped. Return the
 * assigned global sequence (or original for exact retry), 0 on failure. */
uint32_t McuControlEndpoint_PublishActuatorEvent(McuControlEndpoint *endpoint,
    const McuActuatorEventReservation *reservation, uint8_t member,
    uint8_t message_type, const uint8_t *payload_template, size_t length);
size_t McuControlEndpoint_CopyNextActuatorEvent(const McuControlEndpoint *endpoint,
    uint32_t after_sequence, uint8_t *message_type, uint8_t *output, size_t capacity);
/* LOCAL custody API only, not wired to ACK or PROCESS_EVENT_SAVED. Wire custody
 * uses ACTUATOR_EVENT_SAVED and its exact identity. Local callers must also prove
 * durable Pi save first. No business/output effects. */
uint8_t McuControlEndpoint_ConfirmActuatorEventSaved(McuControlEndpoint *endpoint,
    uint8_t message_type, const uint8_t *payload, size_t length);
/* Returns parsed frame count, NOT action or reply count. No copying/replay of
 * requests; arbitrary chunks are drained through the generated bounded parser.
 * Invalid direction/CRC/payload is rejected before state changes.
 */
size_t McuControlEndpoint_Feed(McuControlEndpoint *endpoint, const uint8_t *data,
    size_t length, uint64_t now_ms);
#endif
