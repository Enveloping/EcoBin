#ifndef __USART3_H
#define __USART3_H

#include "stm32f10x.h"
#include "native_serial_buffer.h"
extern NativeRxBuffer NativeHmiRx;
extern NativeRxBuffer NativeHmiTx;
void NativeHmi_InitBuffer(void);
void UART3_SendVisible(char *component, unsigned char visible);

/* UART3 接收缓冲区 */
#define UART3_RX_BUF_SIZE  64
extern unsigned char UART3_RxBuf[UART3_RX_BUF_SIZE];
extern volatile unsigned char UART3_RxLen;

void USART3_Init(void);
void UART3_SendByte(unsigned char SendData);
void UART3_SendString(char *str);
void UART3_SendScreenVal(char *prefix, int value);
void UART3_SendPage(char *page);    /* 串口屏页面跳转: "page <name>" + FF FF FF */
void UART3_SendQRCode(char *url);   /* 发送URL生成二维码: "page0.qr0.txt=\"" + url + "\"" + FF FF FF */
/* Foreground only. A successful return means the complete command was atomically
 * accepted by the USART3 TX ring. It does not wait for USART TC or an HMI ACK. */
uint8_t UART3_TrySendQRCode(const uint8_t *url, uint16_t length);

#endif /* __USART3_H */
