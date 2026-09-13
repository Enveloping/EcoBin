/******************** (C) COPYRIGHT 2019 Designed by Captain *********************
 * 文件名  ：usart3.c
 * 功能    ：USART3 串口通信 (接收命令/发送状态)
 * 实现平台：STM32F103RCT6工控板
 * 硬件连接：------------------------
 *          | PB10 - USART3(Tx)      |
 *          | PB11 - USART3(Rx)      |
 *           ------------------------
 * 店主    ：踏上电子工作室
 * 淘宝店  ：https://shop151358311.taobao.com/
**********************************************************************************/

#include "usart3.h"

NativeRxBuffer NativeHmiRx;
NativeRxBuffer NativeHmiTx;
static uint8_t hmi_drop_frame, hmi_end_count;
void NativeHmi_InitBuffer(void) {
    NativeRx_Init(&NativeHmiRx); NativeRx_Init(&NativeHmiTx);
    hmi_drop_frame = hmi_end_count = 0u;
}

/* UART3 接收缓冲区 */
unsigned char UART3_RxBuf[UART3_RX_BUF_SIZE];
volatile unsigned char UART3_RxLen = 0;

/*
 * USART3 初始化: PB10=TX, PB11=RX, 115200bps
 * 使能接收中断(RXNE)
 */
void USART3_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStructure;
    USART_InitTypeDef USART_InitStructure;

    /* 使能 USART3 和 GPIOB 时钟 */
    RCC_APB1PeriphClockCmd(RCC_APB1Periph_USART3, ENABLE);
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOB, ENABLE);

    /* PB10 = USART3 TX (复用推挽输出) */
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_10;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF_PP;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOB, &GPIO_InitStructure);

    /* PB11 = USART3 RX (浮空输入) */
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_11;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IN_FLOATING;
    GPIO_Init(GPIOB, &GPIO_InitStructure);

    /* UART3 模式配置 */
    USART_InitStructure.USART_BaudRate = 9600;
    USART_InitStructure.USART_WordLength = USART_WordLength_8b;
    USART_InitStructure.USART_StopBits = USART_StopBits_1;
    USART_InitStructure.USART_Parity = USART_Parity_No;
    USART_InitStructure.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
    USART_InitStructure.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;
    USART_Init(USART3, &USART_InitStructure);

    /* 使能接收中断 */
    USART_ITConfig(USART3, USART_IT_RXNE, ENABLE);

    /* 使能 USART3 */
    USART_Cmd(USART3, ENABLE);
}

/*
 * 发送单个字节
 */
void UART3_SendByte(unsigned char SendData)
{
    uint32_t previous = __get_PRIMASK();
    __disable_irq();
    if (hmi_drop_frame) {
        hmi_end_count = SendData == 0xffu ? (uint8_t)(hmi_end_count + 1u) : 0u;
        if (hmi_end_count == 3u) {
            /* End any partially transmitted display instruction before the
             * next full one. No blocking wait or mechanical side effect. */
            NativeRx_PushIrq(&NativeHmiTx, 0xffu);
            NativeRx_PushIrq(&NativeHmiTx, 0xffu);
            NativeRx_PushIrq(&NativeHmiTx, 0xffu);
            hmi_drop_frame = hmi_end_count = 0u;
        }
    } else {
        NativeRx_PushIrq(&NativeHmiTx, SendData);
        if (NativeHmiTx.overflow) {
            (void)NativeRx_DiscardOverflow(&NativeHmiTx);
            hmi_drop_frame = 1u;
            hmi_end_count = SendData == 0xffu ? 1u : 0u;
        }
    }
    USART_ITConfig(USART3, USART_IT_TXE, ENABLE);
    __set_PRIMASK(previous);
}

/*
 * 发送字符串 (以 '\0' 结尾)
 */
void UART3_SendString(char *str)
{
    while(*str)
    {
        UART3_SendByte((unsigned char)*str++);
    }
}

/* Trusted current-page component names only, e.g. n1 / n3. */
void UART3_SendVisible(char *component, unsigned char visible)
{
    UART3_SendString("vis "); UART3_SendString(component);
    UART3_SendByte(','); UART3_SendByte(visible ? '1' : '0');
    UART3_SendByte(0xff); UART3_SendByte(0xff); UART3_SendByte(0xff);
}

/*
 * 发送整数到串口屏 (迪文DWIN指令格式)
 * prefix: 如 "page6.n1.val="
 * value:  要显示的数值
 * 发送字节流: <prefix字符逐个> <value十进制数字> FF FF FF
 * 整串不含\0, 仅以三个0xFF结尾
 */
void UART3_SendScreenVal(char *prefix, int value)
{
    unsigned char i, len;

    /* 发送前缀字符串 (不发送\0, 用\0仅做指针终止判断) */
    while(*prefix)
    {
        UART3_SendByte((unsigned char)*prefix++);
    }

    /* 发送数值 (不依赖\0, 手动拆解十进制位) */
    if(value == 0)
    {
        UART3_SendByte('0');
    }
    else
    {
        unsigned char digits[10];
        int temp = value;
        len = 0;
        while(temp > 0)
        {
            digits[len++] = '0' + (temp % 10);
            temp /= 10;
        }
        for(i = len; i > 0; i--)
        {
            UART3_SendByte(digits[i - 1]);
        }
    }

    /* 发送结尾标识 FF FF FF */
    UART3_SendByte(0xFF);
    UART3_SendByte(0xFF);
    UART3_SendByte(0xFF);
}

/*
 * 串口屏页面跳转 (迪文DWIN指令格式)
 * page: 页面名, 如 "page3"
 * 发送: "page <page>" + FF FF FF
 */
void UART3_SendPage(char *page)
{
    UART3_SendString("page ");
    UART3_SendString(page);
    UART3_SendByte(0xFF);
    UART3_SendByte(0xFF);
    UART3_SendByte(0xFF);
}

/*
 * 发送URL到串口屏生成二维码 (迪文DWIN指令格式)
 * url: 完整的HTTPS URL字符串
 * 发送: "page0.qr0.txt=\"" + url + "\"" + FF FF FF
 * 注: 页面号和变量名需根据实际串口屏工程调整
 */
void UART3_SendQRCode(char *url)
{
    UART3_SendString("page0.qr0.txt=\"");
    UART3_SendString(url);
    UART3_SendByte('"');
    UART3_SendByte(0xFF);
    UART3_SendByte(0xFF);
    UART3_SendByte(0xFF);
}
