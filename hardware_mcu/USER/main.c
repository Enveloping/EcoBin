/* Native MCU entry. USART1 is Pi UART 2.0; USART2 is the Modbus scale.
 * The fixed-frame entry is preserved in main_legacy.c, never co-parsed here.
 * MCU owns mechanics and HMI. Pi saves the complete result before RESULT_SAVED.
 */
#include "stm32f10x.h"
#include "usart1.h"
#include "usart3.h"
#include "adc.h"
#include "led.h"
#include "actuator_runtime.h"
#include "runtime_clock.h"
#include "smoke_monitor.h"
#include "ultrasonic_stm32.h"
#include "mcu_environment_monitor.h"
#include "mcu_delivery_execution.h"
#include "mcu_clean_execution.h"
#include <string.h>

#define DEVICE(field) (ECOBIN_UART_CONFIG_PREIMAGE_DEVICE_OFFSET + ECOBIN_UART_CONFIG_DEVICE_BLOCK_##field##_OFFSET - ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET)
#define START(field) ECOBIN_UART_START_DELIVERY_SESSION_##field##_OFFSET
#define IDLE_FULLNESS_PERIOD_MS 1000u
static McuControlEndpoint control;
static McuWorkPreparation preparation;
static McuDeliveryExecution delivery;
static McuCleanExecution clean;
static McuDeviceEntryUrl device_entry_url;
static uint8_t smoke_enabled = 1u, control_tx_failed, initialization_failed;
static uint8_t display_phase = 0xffu, display_status = 0xffu;
static uint8_t displayed_measurement[16];
static uint32_t display_tick, displayed_scale_attempt, idle_fullness_tick;
static UART3_CommandBatch display_batch;

static uint32_t enter(void) { uint32_t mask = __get_PRIMASK(); __disable_irq(); return mask; }
static void leave(uint32_t mask) { __set_PRIMASK(mask); }
static uint64_t now_ms(void) { uint64_t now; uint32_t mask = enter(); now = RuntimeClock_Now64Locked(); leave(mask); return now; }
static uint8_t pinch(void) { return (uint8_t)((GPIOB->IDR & GPIO_Pin_5) != 0u); }
static void outputs(uint8_t mask) {
    GPIOB->BSRR = (((uint32_t)mask & 7u) << 6) | ((((uint32_t)~mask & 7u) << 6) << 16);
}
static const ActuatorHardware actuators = {enter, leave, pinch, outputs};

static void board_io(void) {
    GPIO_InitTypeDef gpio;
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_GPIOB | RCC_APB2Periph_AFIO, ENABLE);
    GPIO_ResetBits(GPIOB, GPIO_Pin_6 | GPIO_Pin_7 | GPIO_Pin_8);
    gpio.GPIO_Pin = GPIO_Pin_6 | GPIO_Pin_7 | GPIO_Pin_8;
    gpio.GPIO_Speed = GPIO_Speed_10MHz;
    gpio.GPIO_Mode = GPIO_Mode_Out_PP;
    GPIO_Init(GPIOB, &gpio);
    gpio.GPIO_Pin = GPIO_Pin_5; /* PB4 is not a door limit input. */
    gpio.GPIO_Mode = GPIO_Mode_IPD;
    GPIO_Init(GPIOB, &gpio);
}
static void tick_init(void) {
    TIM_TimeBaseInitTypeDef timer;
    NVIC_InitTypeDef irq;
    RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM3, ENABLE);
    TIM_TimeBaseStructInit(&timer);
    timer.TIM_Period = 99u;
    timer.TIM_Prescaler = 7199u;
    TIM_TimeBaseInit(TIM3, &timer);
    TIM_ClearITPendingBit(TIM3, TIM_IT_Update);
    TIM_ITConfig(TIM3, TIM_IT_Update, ENABLE);
    irq.NVIC_IRQChannel = TIM3_IRQn;
    irq.NVIC_IRQChannelPreemptionPriority = 0u;
    irq.NVIC_IRQChannelSubPriority = 0u;
    irq.NVIC_IRQChannelCmd = ENABLE;
    NVIC_Init(&irq);
    TIM_Cmd(TIM3, ENABLE);
}
static void serial_irqs(void) {
    NVIC_InitTypeDef irq;
    irq.NVIC_IRQChannelPreemptionPriority = 0u;
    irq.NVIC_IRQChannelSubPriority = 1u;
    irq.NVIC_IRQChannelCmd = ENABLE;
    irq.NVIC_IRQChannel = USART1_IRQn; NVIC_Init(&irq);
    irq.NVIC_IRQChannel = USART2_IRQn; NVIC_Init(&irq);
    irq.NVIC_IRQChannel = USART3_IRQn; NVIC_Init(&irq);
}
static void send_control(const uint8_t *bytes, size_t length, void *context) {
    (void)context;
    if (!NativeUsart_SendControl(bytes, length)) control_tx_failed = 1u;
}
static uint8_t send_device_entry_url(const uint8_t *url, uint16_t length, void *context) {
    (void)context;
    return UART3_TrySendQRCode(url, length);
}
static uint16_t guard(uint8_t message, const uint8_t *payload, size_t length, uint64_t now, void *context) {
    (void)payload; (void)length; (void)now; (void)context;
    if (initialization_failed || control_tx_failed || ActuatorRuntime_Snapshot().update_latched)
        return ECOBIN_UART_NACK_ERROR_SAFETY_BLOCKED;
    switch (message) {
    case ECOBIN_UART_MESSAGE_CONFIG_BEGIN:
    case ECOBIN_UART_MESSAGE_CONFIG_DEVICE_BLOCK:
    case ECOBIN_UART_MESSAGE_CONFIG_PORT_BLOCK:
    case ECOBIN_UART_MESSAGE_CONFIG_COMMIT:
    case ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_BEGIN:
    case ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_PART:
    case ECOBIN_UART_MESSAGE_DEVICE_ENTRY_URL_COMMIT:
    case ECOBIN_UART_MESSAGE_START_DELIVERY_SESSION:
    case ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION:
    case ECOBIN_UART_MESSAGE_MEASURE_BASELINE:
    case ECOBIN_UART_MESSAGE_DELIVERY_SELECTION:
    case ECOBIN_UART_MESSAGE_CLEAN_UNLOCK_REQUESTED:
    case ECOBIN_UART_MESSAGE_CLEAN_FINISH_REQUESTED:
    case ECOBIN_UART_MESSAGE_CLEAN_COMPLETION_CONFIRMED:
        return ECOBIN_UART_NACK_ERROR_NONE;
    default: return ECOBIN_UART_NACK_ERROR_UNSUPPORTED_MESSAGE;
    }
}
static uint8_t apply_configuration(const McuConfiguration *configuration, void *context) {
    McuConfigWeightPolicy policy;
    (void)context;
    if (!McuConfiguration_ReadWeightPolicy(configuration, 1u, &policy)) return 0u;
    smoke_enabled = configuration->active.preimage[DEVICE(SMOKE_MONITORING_ENABLED)];
    /* Execution/weight/fullness use immutable active settings at each operation.
     * No physical action or artificial sensor success is produced by config. */
    return 1u;
}

static void control_poll(void) {
    uint8_t bytes[32];
    size_t count;
    uint32_t mask = enter();
    if (NativeRx_DiscardOverflow(&NativeControlRx)) {
        ecobin_uart_stream_parser_init(&control.parser, control.parser.sender);
        control.parser.diagnostics |= ECOBIN_UART_DIAG_SEMANTIC_REJECTED;
    }
    leave(mask);
    /* Bounded per turn, so serial floods cannot starve local control/sampling. */
    count = NativeRx_Read(&NativeControlRx, bytes, sizeof(bytes));
    if (count) {
        /* Do not let a background health query make START randomly BUSY or let
         * its response become the new business's first weight. */
        if (preparation.weight.idle_in_flight) {
            uint64_t now = now_ms();
            mask = enter(); NativeScale_Cancel(&NativeScaleRx, now); leave(mask);
            McuWeightRun_CancelIdleAttempt(&preparation.weight, now);
        }
        McuControlEndpoint_Feed(&control, bytes, count, now_ms());
    }
}

static void scale_poll(void) {
    uint8_t bytes[9], ready, can_begin, idle_allowed;
    uint32_t measurement, attempt, mask;
    uint64_t captured, now = now_ms();
    McuConfigWeightPolicy policy;
    ScaleReaderObservation observation;
    ActuatorSnapshot snapshot = ActuatorRuntime_Snapshot();
    idle_allowed = (uint8_t)(!preparation.baseline_active
        && (control.work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || (control.work.phase == ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COUNTDOWN
            && snapshot.door.action_active && snapshot.door.target == MCU_DIRECTION_OPEN)));
    if (preparation.weight.idle_in_flight && !idle_allowed) {
        mask = enter(); NativeScale_Cancel(&NativeScaleRx, now); leave(mask);
        McuWeightRun_CancelIdleAttempt(&preparation.weight, now);
    }
    mask = enter();
    ready = NativeScale_Take(&NativeScaleRx, bytes, &measurement, &attempt, &captured);
    if (!ready) (void)NativeScale_Expire(&NativeScaleRx, now);
    leave(mask);
    if (ready) {
        if (measurement == 0u) (void)McuWeightRun_FinishIdleAttempt(&preparation.weight,
            attempt, captured, now, bytes, sizeof(bytes));
        else (void)McuWeightRun_FinishOwnedAttempt(&preparation.weight,
            measurement, attempt, captured, now, bytes, sizeof(bytes));
    }
    /* Drain actual complete responses before the measurement owner checks time. */
    (void)McuWeightRun_Poll(&preparation.weight, now);
    if (McuWeightRun_CopyObservation(&preparation.weight, &observation))
        (void)McuDeviceFacts_PublishScaleObservation(&control.facts, &observation);
    mask = enter();
    if (NativeScaleRx.active && (!preparation.weight.in_flight
        || (!preparation.weight.idle_in_flight && NativeScaleRx.measurement != preparation.weight.measurement.result.measurement_id)))
        NativeScale_Cancel(&NativeScaleRx, now);
    can_begin = NativeScale_CanBegin(&NativeScaleRx, now);
    leave(mask);
    if (!can_begin) return;
    measurement = preparation.weight.measurement.result.measurement_id;
    policy = preparation.weight.policy;
    if (idle_allowed
        && !McuConfiguration_IsStaging(&preparation.configuration)
        && McuConfiguration_ReadWeightPolicy(&preparation.configuration, 1u, &policy)) {
        attempt = McuWeightRun_StartIdleAttempt(&preparation.weight, &policy, now);
        measurement = 0u; /* A health read is never a business measurement. */
    } else attempt = McuWeightRun_StartOwnedAttempt(&preparation.weight, now);
    if (!attempt) return;
    mask = enter();
    ready = NativeScale_Begin(&NativeScaleRx, measurement, attempt, now, policy.response_timeout_ms);
    leave(mask);
    if (ready) (void)NativeUsart_SendScaleQuery(); /* Failure resolves as unreadable, never a reused old frame. */
}

static void idle_fullness_poll(void) {
    ActuatorSnapshot snapshot;
    if (!preparation.fullness_enabled) return;
    /* Always drain a real raw completion first. A START accepted after the
     * trigger does not discard, relabel or turn that observation into business
     * evidence. The unowned poll cannot consume McuFullnessRun's reservation. */
    (void)McuEnvironmentMonitor_PollUltrasonic(&control.facts);
    snapshot = ActuatorRuntime_Snapshot();
    if (preparation.recovery_active || preparation.baseline_active
        || control.work.status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || control.work.result.held || preparation.fullness.present
        || snapshot.update_latched) return;
    if (!RuntimeClock_PeriodDue(RuntimeClock_Now(), &idle_fullness_tick,
        IDLE_FULLNESS_PERIOD_MS)) return;
    /* Start validates the complete applied configuration/facts identity and
     * uses the real echo timeout. A reserved business group simply refuses
     * this raw begin; failure has no admission or actuator side effect. */
    (void)McuEnvironmentMonitor_StartUltrasonic(&control.facts,
        &preparation.configuration);
}

static void hmi_poll(void) {
    uint8_t bytes[32];
    size_t i, count;
    uint32_t mask = enter();
    (void)NativeRx_DiscardOverflow(&NativeHmiRx);
    leave(mask);
    count = NativeRx_Read(&NativeHmiRx, bytes, sizeof(bytes));
    for (i = 0u; i < count; ++i) {
        if (control.work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING) continue;
        if (preparation.start_message == ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION) {
            if (bytes[i] == 0x05u || bytes[i] == 0x07u)
                (void)McuCleanExecution_Request(&clean, &control,
                    preparation.start_payload + ECOBIN_UART_START_CLEAN_OPERATION_OPERATION_UID_OFFSET,
                    bytes[i] == 0x05u ? ECOBIN_UART_MESSAGE_CLEAN_FINISH_REQUESTED : ECOBIN_UART_MESSAGE_CLEAN_UNLOCK_REQUESTED,
                    clean.action_sequence, now_ms());
        } else if (preparation.start_message == ECOBIN_UART_MESSAGE_START_DELIVERY_SESSION) {
            if ((bytes[i] == 0x02u || bytes[i] == 0x06u)
                && display_phase == ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_SELECTION)
                (void)McuDeliveryExecution_Select(&delivery, &control, displayed_measurement,
                    bytes[i] == 0x02u ? ECOBIN_UART_DELIVERY_SELECTION_END : ECOBIN_UART_DELIVERY_SELECTION_CONTINUE, now_ms());
            if (bytes[i] == 0x00u || bytes[i] == 0x04u)
                (void)McuDeliveryExecution_CloseCurrent(&delivery, &control, now_ms());
            /* 01/03 never bypass START or drive pins from the screen. */
        }
    }
}
static void display_weight(UART3_CommandBatch *batch) {
    int64_t net = (int64_t)delivery.postclose.grams - preparation.initial.grams;
    uint32_t grams = net > 0 ? (uint32_t)net : 0u;
    uint64_t cents;
    cents = ((uint64_t)grams * ecobin_uart_read_u32_be(preparation.start_payload + START(UNIT_PRICE_TEN_THOUSANDTHS))) / 100000u;
    (void)UART3_CommandBatchAppendScreenVal(batch, "page7.n1.val=", (int)(grams / 1000u));
    (void)UART3_CommandBatchAppendScreenVal(batch, "page7.n3.val=", (int)((grams % 1000u) / 100u));
    (void)UART3_CommandBatchAppendScreenVal(batch, "page7.n2.val=", (int)(cents / 100u));
    (void)UART3_CommandBatchAppendScreenVal(batch, "page7.n4.val=", (int)((cents / 10u) % 10u));
    (void)UART3_CommandBatchAppendScreenVal(batch, "page7.n5.val=", (int)(cents % 10u));
}
static void display_price(UART3_CommandBatch *batch, const char *prefix) {
    uint32_t price = ecobin_uart_read_u32_be(preparation.start_payload + START(UNIT_PRICE_TEN_THOUSANDTHS));
    /* The existing screen has a fixed "0." plus one numeric component. Show
     * only a price this layout can represent exactly, never the previous price.
     * General decimal-price formatting remains a small HMI integration item. */
    uint8_t supported = (uint8_t)(price < 10000u && price % 1000u == 0u);
    (void)UART3_CommandBatchAppendScreenVal(batch, prefix,
        supported ? (int)(price / 1000u) : 0);
    (void)UART3_CommandBatchAppendVisible(batch, "n0", supported);
}
static void display_poll(void) {
    uint8_t phase = control.work.phase, status = control.work.status;
    uint8_t has_batch = 1u;
    uint32_t mask, now;
    if (phase == display_phase && status == display_status) return;
    UART3_CommandBatchInit(&display_batch);
    if (status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING) {
        (void)UART3_CommandBatchAppendPage(&display_batch, "page0");
    }
    if (preparation.start_message == ECOBIN_UART_MESSAGE_START_CLEAN_OPERATION) {
        if (status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING)
            (void)UART3_CommandBatchAppendPage(&display_batch, "page8");
    } else if (status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING) {
        switch (phase) {
        case ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COMMAND:
            (void)UART3_CommandBatchAppendPage(&display_batch, "page4");
            break;
        case ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COUNTDOWN:
            (void)UART3_CommandBatchAppendPage(&display_batch, "page6");
            display_price(&display_batch, "page6.n0.val=");
            (void)UART3_CommandBatchAppendScreenVal(&display_batch, "page6.n1.val=", 0);
            (void)UART3_CommandBatchAppendScreenVal(&display_batch, "page6.n3.val=", 0);
            (void)UART3_CommandBatchAppendVisible(&display_batch, "n1", 0u);
            (void)UART3_CommandBatchAppendVisible(&display_batch, "n3", 0u);
            break;
        case ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_SELECTION:
            (void)UART3_CommandBatchAppendPage(&display_batch, "page7");
            display_price(&display_batch, "page7.n0.val=");
            display_weight(&display_batch);
            break;
        default:
            has_batch = 0u;
            break;
        }
    }
    if (has_batch && !UART3_TrySendBatch(&display_batch)) return;
    /* Only a fully queued page/init batch retires the previous display phase.
     * RX is then cleared once, so old-page bytes cannot enter the new round. */
    mask = enter(); NativeHmiRx.tail = NativeHmiRx.head; leave(mask);
    display_phase = phase;
    display_status = status;
    if (status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        && phase == ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COUNTDOWN) {
        displayed_scale_attempt = preparation.weight.attempt_sequence;
        now = RuntimeClock_Now();
        display_tick = now - 250u;
    } else if (status == ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        && phase == ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_WAIT_SELECTION)
        memcpy(displayed_measurement, delivery.postclose.uid, 16u);
}

static void display_live_weight(void) {
    ScaleReaderObservation observation;
    ActuatorDeliveryCycle cycle;
    uint64_t now, deadline;
    int64_t delta;
    uint32_t grams, tick;
    if (display_phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COUNTDOWN
        || control.work.phase != ECOBIN_UART_MCU_WORK_PHASE_DELIVERY_OPEN_COUNTDOWN
        || control.work.status != ECOBIN_UART_WORK_QUERY_STATUS_RUNNING
        || (uint32_t)(RuntimeClock_Now() - display_tick) < 250u) return;
    tick = RuntimeClock_Now();
    UART3_CommandBatchInit(&display_batch);
    now = now_ms(); cycle = ActuatorRuntime_DeliveryCycle();
    deadline = cycle.opened_at_ms + ecobin_uart_read_u32_be(preparation.start_payload + START(DELIVERY_AUTO_CLOSE_MS));
    (void)UART3_CommandBatchAppendScreenVal(&display_batch, "page6.n2.val=",
        (int)(now < deadline ? (deadline - now + 999u) / 1000u : 0u));
    if (!McuWeightRun_CopyObservation(&preparation.weight, &observation)
        || observation.status != SCALE_READER_OK || observation.captured_ms > now
        || now - observation.captured_ms > preparation.weight.policy.measurement.maximum_age_ms) {
        (void)UART3_CommandBatchAppendVisible(&display_batch, "n1", 0u);
        (void)UART3_CommandBatchAppendVisible(&display_batch, "n3", 0u);
        if (UART3_TrySendBatch(&display_batch)) display_tick = tick;
        return;
    }
    if (observation.attempt_sequence <= displayed_scale_attempt) {
        if (UART3_TrySendBatch(&display_batch)) display_tick = tick;
        return;
    }
    delta = (int64_t)observation.grams - preparation.initial.grams;
    grams = delta > 0 ? (uint32_t)delta : 0u;
    (void)UART3_CommandBatchAppendScreenVal(&display_batch, "page6.n1.val=", (int)(grams / 1000u));
    (void)UART3_CommandBatchAppendScreenVal(&display_batch, "page6.n3.val=", (int)((grams % 1000u) / 100u));
    (void)UART3_CommandBatchAppendVisible(&display_batch, "n1", 1u);
    (void)UART3_CommandBatchAppendVisible(&display_batch, "n3", 1u);
    if (UART3_TrySendBatch(&display_batch)) {
        display_tick = tick;
        displayed_scale_attempt = observation.attempt_sequence;
    }
    /* Display-only delta, not a new first/final measurement or business result. */
}

int main(void) {
    SystemInit();
    NVIC_PriorityGroupConfig(NVIC_PriorityGroup_0);
    board_io();
    RuntimeClock_Init();
    ActuatorRuntime_Init(&actuators);
    (void)ActuatorRuntime_SetDoorTarget(MCU_DIRECTION_CLOSE);
    tick_init(); /* Pinch/timed outputs are alive before optional ADC initialization. */
    NativeUsart_InitBuffers(); NativeHmi_InitBuffer();
    USART1_Init(); USART2_int(); USART3_Init();
    serial_irqs();
    LED_GPIO_Config();
    (void)ADC1_TryInit(); SmokeMonitor_Init();
    McuControlEndpoint_Init(&control, 1u, send_control, 0);
    initialization_failed = (uint8_t)(!McuWorkPreparation_Attach(&preparation, &control, 1u, guard, 0)
        || !McuWorkPreparation_AttachDeviceEntryUrl(&preparation, &control,
            &device_entry_url, send_device_entry_url, 0)
        || !McuDeliveryExecution_Attach(&delivery, &preparation, &control)
        || !McuCleanExecution_Attach(&clean, &preparation, &control)
        || !McuWorkPreparation_SetConfigurationApply(&preparation, &control, apply_configuration, 0));
    if (UltrasonicStm32_Init()) (void)McuWorkPreparation_AttachFullness(&preparation, &control);
    USART1_RX_IntEnable();
    for (;;) {
        scale_poll();
        hmi_poll(); /* Old unscoped screen bytes are consumed before a new START. */
        control_poll();
        idle_fullness_poll();
        (void)McuWorkPreparation_Poll(&preparation, &control, now_ms());
        if (smoke_enabled) (void)McuEnvironmentMonitor_PollSmoke(&control.facts);
        display_poll();
        display_live_weight();
        /* No Delay: timer owns mechanical timing and each producer is bounded. */
    }
}
