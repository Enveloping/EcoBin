#ifndef __USART3_H
#define __USART3_H

#include "stm32f10x.h"

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

#endif /* __USART3_H */
