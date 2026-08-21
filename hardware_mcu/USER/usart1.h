#ifndef __USART1_H
#define	__USART1_H

#include "stm32f10x.h"

/* RS485 接收缓冲区 */
#define RS485_RX_BUF_SIZE  64
extern unsigned char RS485_RxBuf[RS485_RX_BUF_SIZE];
extern volatile unsigned char RS485_RxLen;

/* USART1 视觉模块接收缓冲区：A0=195字节，8位长度计数器上限为255。 */
#define VISION_RX_BUF_SIZE  255
extern unsigned char Vision_RxBuf[VISION_RX_BUF_SIZE];
extern volatile unsigned char Vision_RxLen;

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

/* ===== URL 存储 (A0 帧) ===== */
#define URL_BUF_SIZE  200
extern unsigned char url_buffer[URL_BUF_SIZE];
extern unsigned char url_len;

void USART1_Init(void);
void USART1_RX_IntEnable(void);  /* 使能USART1接收中断 */
void RS485_SendByte(unsigned char SendData);
void RS485_SendBuf(unsigned char *buf, unsigned char len);
unsigned short CRC16_Modbus(unsigned char *buf, unsigned char len);
unsigned char Weight_Read(unsigned long *weight);
void USART2_int(void);

/* 非阻塞称重接口 */
void Weight_Read_Start(void);
unsigned char Weight_Read_Poll(unsigned long *weight);

/* 帧长度查询 (协议v2.0 接收解析用) */
unsigned char GetRxFrameLen(unsigned char header);

/* ===== 视觉模块通信 (USART1) ===== */
void Vision_SendPushRod(unsigned char status);   /* AA+status+AA 推杆状态上报(调试用) */
void Vision_SendOverflow(unsigned char status);  /* BB+status+BB 溢满标志(保留兼容) */
void Vision_SendSmoke(unsigned char status);     /* CC+status+CC 烟雾状态上报(00/01/02) */
void Vision_SendWeight(unsigned long weight);    /* DD+weight(3字节)+DD 旧版5字节(保留兼容) */

/* ===== 协议v2.0: 投递/清运结果帧 (9字节) ===== */
void Vision_SendDeliveryResult(unsigned long pre_weight, unsigned long post_weight, unsigned char full);
void Vision_SendCleaningResult(unsigned long pre_weight, unsigned long post_weight, unsigned char full);

/* ===== 协议v2.0: F1 传感器自检应答 (8字节) ===== */
void Vision_SendSelfTestResult(unsigned char valid, unsigned long weight,
                               unsigned char dist, unsigned char smoke);

/* ===== fixed-frame revision 2: F3 firmware/status snapshot (51 bytes) ===== */
void Vision_SendFirmwareStatus(unsigned char mode, unsigned char status,
                               unsigned char safe_flags);

#endif /* __USART1_H */
