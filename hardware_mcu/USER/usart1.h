#ifndef __USART1_H
#define	__USART1_H

#include "stm32f10x.h"
#include <stdio.h>
#include "uar/ecobin_uart_protocol.h"

/* RS485 接收缓冲区 */
#define RS485_RX_BUF_SIZE  64
extern unsigned char RS485_RxBuf[RS485_RX_BUF_SIZE];
extern volatile unsigned char RS485_RxLen;

/* 定时器全局变量 (main.c 定义) */
extern volatile unsigned char  g_weight_tick;
extern volatile unsigned short g_tick_count;

/* ===== 非阻塞称重状态机 ===== */
typedef enum {
    WEIGHT_IDLE = 0,   /* 空闲: 等待定时器触发 */
    WEIGHT_SENT,       /* 已发送Modbus命令, 等待应答 */
    WEIGHT_DONE        /* 数据就绪(成功/超时/错误) */
} WeightState;

extern WeightState weight_state;

void USART1_Init(void);
void USART1_RX_IntEnable(void);

/* ===== RS485 称重通信 (USART2) ===== */
void RS485_SendByte(unsigned char SendData);
void RS485_SendBuf(unsigned char *buf, unsigned char len);
unsigned short CRC16_Modbus(unsigned char *buf, unsigned char len);
unsigned char Weight_Read(unsigned short *weight);
void USART2_int(void);

/* 非阻塞称重接口 */
void Weight_Read_Start(void);
unsigned char Weight_Read_Poll(unsigned short *weight);

/* =====================================================================
 * ecobin UART 协议传输层 (USART1 ↔ 橙派/Edge)
 * 替换原有的 AA/BB/CC/DD 简单帧协议
 * ===================================================================== */

/* Transport state */
typedef enum {
    ECOBIN_TRANSPORT_UNINIT     = 0,
    ECOBIN_TRANSPORT_HELLO_SENT = 1,
    ECOBIN_TRANSPORT_READY      = 2
} ecobin_transport_state_t;

/* Received message, for application dispatch */
typedef struct {
    uint8_t                    type;
    const uint8_t             *payload;
    uint16_t                   payload_length;
    uint32_t                   tx_sequence;
} ecobin_transport_rx_msg_t;

/* Init: call once at startup after USART1_Init + USART1_RX_IntEnable */
void ecobin_transport_init(
    const char *firmware_identity,
    const char *firmware_version);

/* ISR: feed one received byte (call from USART1_IRQHandler) */
void ecobin_transport_feed_byte(uint8_t byte);

/* ISR: increment uptime by 100 ms (call from TIM3_IRQHandler) */
void ecobin_transport_tick_100ms(void);

/* Main loop: poll for received messages. Returns 1 when msg is ready. */
int  ecobin_transport_poll(ecobin_transport_rx_msg_t *rx_msg);

/* State query */
ecobin_transport_state_t ecobin_transport_get_state(void);

/* High-level send functions (MCU -> Edge), synchronous blocking TX */

void ecobin_transport_send_door_state(
    ecobin_uart_delivery_door_state_t  state,
    ecobin_uart_delivery_door_health_t health);

void ecobin_transport_send_safety_sensor_event(
    ecobin_uart_smoke_state_t   smoke_state,
    ecobin_uart_sensor_health_t sensor_health,
    uint16_t                    fault_code);

void ecobin_transport_send_postclose_weight(
    int32_t                          stable_weight_grams,
    int32_t                          last_observed_weight_grams,
    ecobin_uart_measurement_status_t status,
    ecobin_uart_sensor_health_t      sensor_health,
    uint16_t                         fault_code);

void ecobin_transport_send_fullness_result(
    ecobin_uart_infrared_value_t      infrared_value,
    ecobin_uart_sensor_health_t       infrared_health,
    int32_t                           stable_weight_grams,
    ecobin_uart_measurement_status_t  measurement_status,
    ecobin_uart_sensor_health_t       weight_sensor_health,
    uint16_t                          fault_code);

void ecobin_transport_send_fault(
    ecobin_uart_fault_component_t component,
    ecobin_uart_fault_severity_t  severity,
    ecobin_uart_fault_lifecycle_t lifecycle,
    uint16_t                      fault_code);

#endif /* __USART1_H */
