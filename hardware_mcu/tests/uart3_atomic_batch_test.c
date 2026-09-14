#include "usart3.h"
#include <assert.h>
#include <string.h>

static uint32_t primask;
static uint32_t disable_calls;
static uint32_t restore_calls;
static uint32_t txe_enable_calls;

uint32_t HostGetPrimask(void) { return primask; }
void HostDisableIrq(void) { primask = 1u; ++disable_calls; }
void HostSetPrimask(uint32_t value) { primask = value; ++restore_calls; }

void RCC_APB1PeriphClockCmd(uint32_t peripheral, FunctionalState state)
{ (void)peripheral; (void)state; }
void RCC_APB2PeriphClockCmd(uint32_t peripheral, FunctionalState state)
{ (void)peripheral; (void)state; }
void GPIO_Init(GPIO_TypeDef *gpio, GPIO_InitTypeDef *configuration)
{ (void)gpio; (void)configuration; }
void USART_Init(USART_TypeDef *uart, USART_InitTypeDef *configuration)
{ (void)uart; (void)configuration; }
void USART_Cmd(USART_TypeDef *uart, FunctionalState state)
{ (void)uart; (void)state; }
void USART_ITConfig(USART_TypeDef *uart, uint16_t interrupt,
    FunctionalState state)
{
    (void)uart;
    if (interrupt == USART_IT_TXE && state == ENABLE) ++txe_enable_calls;
}

static size_t drain(uint8_t *bytes, size_t capacity)
{
    return NativeRx_Read(&NativeHmiTx, bytes, capacity);
}

int main(void)
{
    UART3_CommandBatch batch;
    uint8_t output[NATIVE_RX_CAPACITY];
    uint8_t existing[500];
    uint16_t head;
    uint32_t i, enables;
    size_t length;
    static const uint8_t expected_batch[] =
        "page page6\xff\xff\xff"
        "page6.n1.val=12\xff\xff\xff"
        "vis n1,1\xff\xff\xff";
    static const char url[] = "https://device.example/entry";
    static const uint8_t expected_qr[] =
        "page0.qr0.txt=\"https://device.example/entry\"\xff\xff\xff";
    char oversized[UART3_COMMAND_BATCH_CAPACITY + 1u];

    NativeHmi_InitBuffer();
    UART3_CommandBatchInit(&batch);
    assert(UART3_CommandBatchAppendPage(&batch, "page6"));
    assert(UART3_CommandBatchAppendScreenVal(&batch, "page6.n1.val=", 12));
    assert(UART3_CommandBatchAppendVisible(&batch, "n1", 1u));
    assert(UART3_TrySendBatch(&batch));
    assert(disable_calls == 1u && restore_calls == 1u && primask == 0u);
    assert(txe_enable_calls == 1u);
    length = drain(output, sizeof(output));
    assert(length == sizeof(expected_batch) - 1u);
    assert(memcmp(output, expected_batch, length) == 0);

    /* The largest current one-time result-page shape remains one batch even
     * with ten decimal digits in every numeric component. */
    NativeHmi_InitBuffer();
    UART3_CommandBatchInit(&batch);
    assert(UART3_CommandBatchAppendPage(&batch, "page7"));
    assert(UART3_CommandBatchAppendScreenVal(&batch,
        "page7.n0.val=", 2147483647));
    assert(UART3_CommandBatchAppendVisible(&batch, "n0", 1u));
    assert(UART3_CommandBatchAppendScreenVal(&batch,
        "page7.n1.val=", 2147483647));
    assert(UART3_CommandBatchAppendScreenVal(&batch,
        "page7.n3.val=", 2147483647));
    assert(UART3_CommandBatchAppendScreenVal(&batch,
        "page7.n2.val=", 2147483647));
    assert(UART3_CommandBatchAppendScreenVal(&batch,
        "page7.n4.val=", 2147483647));
    assert(UART3_CommandBatchAppendScreenVal(&batch,
        "page7.n5.val=", 2147483647));
    assert(batch.valid && batch.length <= UART3_COMMAND_BATCH_CAPACITY);
    assert(UART3_TrySendBatch(&batch));
    assert(drain(output, sizeof(output)) == batch.length);

    /* Insufficient capacity rejects the complete page/init batch before any
     * byte or head publication, and does not arm TXE as a false success. */
    NativeHmi_InitBuffer();
    memset(existing, 0x5au, sizeof(existing));
    for (i = 0u; i < sizeof(existing); ++i)
        NativeRx_PushIrq(&NativeHmiTx, existing[i]);
    head = NativeHmiTx.head;
    enables = txe_enable_calls;
    UART3_CommandBatchInit(&batch);
    assert(UART3_CommandBatchAppendPage(&batch, "page0"));
    assert(!UART3_TrySendBatch(&batch));
    assert(NativeHmiTx.head == head);
    assert(txe_enable_calls == enables);
    length = drain(output, sizeof(output));
    assert(length == sizeof(existing));
    assert(memcmp(output, existing, length) == 0);

    /* The production QR helper and ordinary batches share the same FIFO. A
     * later rejected page batch cannot truncate or overwrite the accepted QR. */
    NativeHmi_InitBuffer();
    assert(UART3_TrySendQRCode((const uint8_t *)url,
        (uint16_t)(sizeof(url) - 1u)));
    for (i = 0u; i < 510u - (sizeof(expected_qr) - 1u); ++i)
        NativeRx_PushIrq(&NativeHmiTx, (uint8_t)i);
    UART3_CommandBatchInit(&batch);
    assert(UART3_CommandBatchAppendPage(&batch, "page7"));
    assert(!UART3_TrySendBatch(&batch));
    length = drain(output, sizeof(output));
    assert(length == 510u);
    assert(memcmp(output, expected_qr, sizeof(expected_qr) - 1u) == 0);

    /* A batch that cannot be represented locally is invalid and cannot expose
     * its partial prefix to the TX queue. */
    memset(oversized, 'a', sizeof(oversized));
    oversized[sizeof(oversized) - 1u] = '\0';
    NativeHmi_InitBuffer();
    UART3_CommandBatchInit(&batch);
    assert(!UART3_CommandBatchAppendPage(&batch, oversized));
    assert(!UART3_TrySendBatch(&batch));
    assert(drain(output, sizeof(output)) == 0u);
    return 0;
}
