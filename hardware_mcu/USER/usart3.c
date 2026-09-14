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
#include <string.h>

NativeRxBuffer NativeHmiRx;
NativeRxBuffer NativeHmiTx;
typedef char uart3_batch_fits_tx_ring[
    (UART3_COMMAND_BATCH_CAPACITY <= NATIVE_RX_CAPACITY - 1u) ? 1 : -1];
void NativeHmi_InitBuffer(void) {
    NativeRx_Init(&NativeHmiRx); NativeRx_Init(&NativeHmiTx);
}

static uint8_t hmi_try_write(const NativeSerialSpan *spans, size_t count)
{
    uint32_t previous = __get_PRIMASK();
    uint8_t queued;
    __disable_irq();
    queued = NativeRx_WriteAtomic(&NativeHmiTx, spans, count);
    if (queued) USART_ITConfig(USART3, USART_IT_TXE, ENABLE);
    __set_PRIMASK(previous);
    return queued;
}

static uint8_t batch_append(UART3_CommandBatch *batch,
    const uint8_t *bytes, size_t length)
{
    if (batch == 0 || !batch->valid
        || batch->length > UART3_COMMAND_BATCH_CAPACITY
        || (bytes == 0 && length != 0u)
        || length > (size_t)UART3_COMMAND_BATCH_CAPACITY - batch->length) {
        if (batch != 0) batch->valid = 0u;
        return 0u;
    }
    if (length != 0u) memcpy(batch->bytes + batch->length, bytes, length);
    batch->length = (uint16_t)(batch->length + length);
    return 1u;
}

static uint8_t batch_append_text(UART3_CommandBatch *batch, const char *text)
{
    if (text == 0) {
        if (batch != 0) batch->valid = 0u;
        return 0u;
    }
    return batch_append(batch, (const uint8_t *)text, strlen(text));
}

void UART3_CommandBatchInit(UART3_CommandBatch *batch)
{
    if (batch == 0) return;
    batch->length = 0u;
    batch->valid = 1u;
}

uint8_t UART3_CommandBatchAppendVisible(UART3_CommandBatch *batch,
    const char *component, uint8_t visible)
{
    static const uint8_t prefix[] = "vis ";
    static const uint8_t comma[] = {','};
    static const uint8_t suffix[] = {0xffu, 0xffu, 0xffu};
    uint8_t state = visible ? '1' : '0';
    if (component == 0) {
        if (batch != 0) batch->valid = 0u;
        return 0u;
    }
    return (uint8_t)(batch_append(batch, prefix, sizeof(prefix) - 1u)
        && batch_append_text(batch, component)
        && batch_append(batch, comma, sizeof(comma))
        && batch_append(batch, &state, 1u)
        && batch_append(batch, suffix, sizeof(suffix)));
}

uint8_t UART3_CommandBatchAppendScreenVal(UART3_CommandBatch *batch,
    const char *prefix, int value)
{
    static const uint8_t suffix[] = {0xffu, 0xffu, 0xffu};
    uint8_t digits[10];
    uint8_t first = sizeof(digits);
    unsigned int number = value > 0 ? (unsigned int)value : 0u;
    if (prefix == 0) {
        if (batch != 0) batch->valid = 0u;
        return 0u;
    }
    do {
        digits[--first] = (uint8_t)('0' + number % 10u);
        number /= 10u;
    } while (number != 0u && first != 0u);
    return (uint8_t)(batch_append_text(batch, prefix)
        && batch_append(batch, digits + first, sizeof(digits) - first)
        && batch_append(batch, suffix, sizeof(suffix)));
}

uint8_t UART3_CommandBatchAppendPage(UART3_CommandBatch *batch,
    const char *page)
{
    static const uint8_t prefix[] = "page ";
    static const uint8_t suffix[] = {0xffu, 0xffu, 0xffu};
    if (page == 0) {
        if (batch != 0) batch->valid = 0u;
        return 0u;
    }
    return (uint8_t)(batch_append(batch, prefix, sizeof(prefix) - 1u)
        && batch_append_text(batch, page)
        && batch_append(batch, suffix, sizeof(suffix)));
}

uint8_t UART3_TrySendBatch(const UART3_CommandBatch *batch)
{
    NativeSerialSpan span;
    if (batch == 0 || !batch->valid || batch->length == 0u
        || batch->length > UART3_COMMAND_BATCH_CAPACITY) return 0u;
    span.bytes = batch->bytes;
    span.length = batch->length;
    return hmi_try_write(&span, 1u);
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
    NativeSerialSpan span;
    span.bytes = &SendData;
    span.length = 1u;
    (void)hmi_try_write(&span, 1u);
}

/*
 * 发送字符串 (以 '\0' 结尾)
 */
void UART3_SendString(char *str)
{
    NativeSerialSpan span;
    if (str == 0) return;
    span.bytes = (const uint8_t *)str;
    span.length = strlen(str);
    (void)hmi_try_write(&span, 1u);
}

/* Trusted current-page component names only, e.g. n1 / n3. */
void UART3_SendVisible(char *component, unsigned char visible)
{
    UART3_CommandBatch batch;
    UART3_CommandBatchInit(&batch);
    if (UART3_CommandBatchAppendVisible(&batch, component, visible))
        (void)UART3_TrySendBatch(&batch);
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
    UART3_CommandBatch batch;
    UART3_CommandBatchInit(&batch);
    if (UART3_CommandBatchAppendScreenVal(&batch, prefix, value))
        (void)UART3_TrySendBatch(&batch);
}

/*
 * 串口屏页面跳转 (迪文DWIN指令格式)
 * page: 页面名, 如 "page3"
 * 发送: "page <page>" + FF FF FF
 */
void UART3_SendPage(char *page)
{
    UART3_CommandBatch batch;
    UART3_CommandBatchInit(&batch);
    if (UART3_CommandBatchAppendPage(&batch, page))
        (void)UART3_TrySendBatch(&batch);
}

/*
 * 发送URL到串口屏生成二维码 (迪文DWIN指令格式)
 * url: 完整的HTTPS URL字符串
 * 发送: "page0.qr0.txt=\"" + url + "\"" + FF FF FF
 * 注: 页面号和变量名需根据实际串口屏工程调整
 */
void UART3_SendQRCode(char *url)
{
    if (url != 0)
        (void)UART3_TrySendQRCode((const uint8_t *)url, (uint16_t)strlen(url));
}

uint8_t UART3_TrySendQRCode(const uint8_t *url, uint16_t length)
{
    static const uint8_t prefix[] = "page0.qr0.txt=\"";
    static const uint8_t suffix[] = {'\"', 0xffu, 0xffu, 0xffu};
    NativeSerialSpan spans[3];
    uint16_t index;
    if (url == 0 || length == 0u || length > 192u || length < 8u
        || memcmp(url, "https://", 8u) != 0) return 0u;
    for (index = 0u; index < length; ++index)
        if (url[index] < 0x21u || url[index] > 0x7eu
            || url[index] == 0x22u || url[index] == 0x5cu) return 0u;
    spans[0].bytes = prefix; spans[0].length = sizeof(prefix) - 1u;
    spans[1].bytes = url; spans[1].length = length;
    spans[2].bytes = suffix; spans[2].length = sizeof(suffix);
    return hmi_try_write(spans, 3u);
}
