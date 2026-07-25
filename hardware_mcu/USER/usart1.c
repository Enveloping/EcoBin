/******************** (C) COPYRIGHT 2019 Designed by Captain *********************
 * �ļ���  ��usart1.c
 * ����    ��USART2 RS485ͨ�� (Modbus RTU)
 * ʵ��ƽ̨��STM32F103RCT6���ذ�
 * Ӳ�����ӣ�------------------------
 *          | PA2  - USART2(Tx)      |
 *          | PA3  - USART2(Rx)      |
 *          | PA1  - RS485 RE/DE     |
 *           ------------------------
 * ����    ��̤�ϵ��ӹ�����
 * �Ա��꣺https://shop151358311.taobao.com/
**********************************************************************************/

#if defined(ECOBIN_TRANSPORT_HOST_TEST)
#include "ecobin_transport.h"
#else
#include "usart1.h"
#endif

#if !defined(ECOBIN_TRANSPORT_HOST_TEST)
/* RS485�������: RE/DE����PA1 */
#define Set_RE  GPIO_SetBits(GPIOA,GPIO_Pin_1);
#define Clr_RE  GPIO_ResetBits(GPIOA,GPIO_Pin_1);
#define Set_DE  GPIO_SetBits(GPIOA,GPIO_Pin_1);
#define Clr_DE  GPIO_ResetBits(GPIOA,GPIO_Pin_1);

/* RS485 ���ջ����� */
unsigned char RS485_RxBuf[RS485_RX_BUF_SIZE];
volatile unsigned char RS485_RxLen = 0;

/* printf �ض��� USART1, ���Դ�ӡ�� */
int fputc(int ch, FILE *f)
{
	USART_SendData(USART1, (unsigned char)ch);
	while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
	return ch;
}

/* USART1 ��ʼ��: PA9=TX, PA10=RX, 115200bps ���Դ��� */
void USART1_Init(void)
{
	GPIO_InitTypeDef GPIO_InitStructure;
	USART_InitTypeDef USART_InitStructure;

	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_USART1, ENABLE);

	/* PA9 = USART1 TX */
	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_9;
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF_PP;
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
	GPIO_Init(GPIOA, &GPIO_InitStructure);

	/* PA10 = USART1 RX (上拉防止悬空噪声) */
	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_10;
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IPU;
	GPIO_Init(GPIOA, &GPIO_InitStructure);

	USART_InitStructure.USART_BaudRate = 115200;
	USART_InitStructure.USART_WordLength = USART_WordLength_8b;
	USART_InitStructure.USART_StopBits = USART_StopBits_1;
	USART_InitStructure.USART_Parity = USART_Parity_No;
	USART_InitStructure.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
	USART_InitStructure.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;
	USART_Init(USART1, &USART_InitStructure);

	USART_Cmd(USART1, ENABLE);
}

/* 使能USART1接收中断 (视觉模块通信) */
void USART1_RX_IntEnable(void)
{
    USART_ITConfig(USART1, USART_IT_RXNE, ENABLE);
}

void DelayNuS(unsigned int i)
{
	unsigned char t = 0;
	for(;i>0;i--)
	{
		for(t=0;t<2;t++)
		{
		}
	}
}

void USART2_Config(void)
{
	GPIO_InitTypeDef GPIO_InitStructure;
	USART_InitTypeDef USART_InitStructure;

	/* ʹ�� USART2 ʱ�� */
	RCC_APB1PeriphClockCmd(RCC_APB1Periph_USART2, ENABLE);
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);

	/* RS485����������� PA1 */
	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_1 ;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_10MHz;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_Out_PP;
    GPIO_Init(GPIOA, &GPIO_InitStructure);

	/* USART2 ʹ��IO�˿����� */
  GPIO_InitStructure.GPIO_Pin = GPIO_Pin_2;
  GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF_PP; //�����������
  GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
  GPIO_Init(GPIOA, &GPIO_InitStructure);

  GPIO_InitStructure.GPIO_Pin = GPIO_Pin_3;
  GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IN_FLOATING;	//��������
  GPIO_Init(GPIOA, &GPIO_InitStructure);   //��ʼ��GPIOA

	/* UART2 ����ģʽ���� */
	USART_InitStructure.USART_BaudRate = 115200;	//����������: 115200(����ģ��)
	USART_InitStructure.USART_WordLength = USART_WordLength_8b;	//����λ: 8λ
	USART_InitStructure.USART_StopBits = USART_StopBits_1; 	//ֹͣλ: 1λ
	USART_InitStructure.USART_Parity = USART_Parity_No ;  //У��: ��
	USART_InitStructure.USART_HardwareFlowControl = USART_HardwareFlowControl_None;	//Ӳ������: ��
	USART_InitStructure.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;//���ͺͽ��ն�ʹ��
	USART_Init(USART2, &USART_InitStructure);  //��ʼ��USART2

    /* ʹ�ܴ���2�����ж� */
  USART_ITConfig(USART2, USART_IT_RXNE, ENABLE);

	USART_Cmd(USART2, ENABLE);// UART2ʹ��
}

 /* ����һ���ֽ����� */
 void RS485_SendByte(unsigned char SendData)
{
		Set_DE;
		Set_RE;
		DelayNuS(5000);
		USART_SendData(USART2,SendData);
		while(USART_GetFlagStatus(USART2, USART_FLAG_TXE) == RESET);
		DelayNuS(5000);
		Clr_DE;
		Clr_RE;
}

/* ���Ͷ���ֽ�����(һ�η������) */
void RS485_SendBuf(unsigned char *buf, unsigned char len)
{
	unsigned char i;
	Set_DE;
	Set_RE;
	DelayNuS(5000);  //�����л���ȴ��ȶ�
	for(i = 0; i < len; i++)
	{
		USART_SendData(USART2, buf[i]);
		while(USART_GetFlagStatus(USART2, USART_FLAG_TXE) == RESET);
	}
	//�ȴ����һ���ֽڷ������
	while(USART_GetFlagStatus(USART2, USART_FLAG_TC) == RESET);
	DelayNuS(5000);  //�ȴ������ͷ�
	Clr_DE;
	Clr_RE;
}

/* Modbus CRC16 У�� */
unsigned short CRC16_Modbus(unsigned char *buf, unsigned char len)
{
	unsigned short crc = 0xFFFF;
	unsigned char i;
	while(len--)
	{
		crc ^= *buf++;
		for(i = 0; i < 8; i++)
		{
			if(crc & 0x0001)
			{
				crc >>= 1;
				crc ^= 0xA001;
			}
			else
			{
				crc >>= 1;
			}
		}
	}
	return crc;
}

/*
 * ��ȡ����ģ������
 * ����: 01 03 00 00 00 02 C4 0B
 * ��Ӧ: 01 03 04 Data_H Data_L 00 00 CRC_L CRC_H
 * ����: weight - ��������ֵ
 * ����: 0=�ɹ�, 1=��ʱ, 2=����̫��, 3=CRC����
 */
unsigned char Weight_Read(unsigned short *weight)
{
	unsigned char cmd[8];
	unsigned short crc;
	unsigned short timeout;
	unsigned char i;

	/* ����Modbus�����ּĴ���ָ�� */
	cmd[0] = 0x01;  //�ӻ���ַ
	cmd[1] = 0x03;  //������: �����ּĴ���
	cmd[2] = 0x00;  //��ʼ��ַ���ֽ�
	cmd[3] = 0x00;  //��ʼ��ַ���ֽ�
	cmd[4] = 0x00;  //�Ĵ����������ֽ�
	cmd[5] = 0x02;  //�Ĵ����������ֽ�
	crc = CRC16_Modbus(cmd, 6);
	cmd[6] = crc & 0xFF;        //CRC���ֽ�
	cmd[7] = (crc >> 8) & 0xFF; //CRC���ֽ�

	/* ��ս��ջ����� */
	for(i = 0; i < RS485_RX_BUF_SIZE; i++)
		RS485_RxBuf[i] = 0;
	RS485_RxLen = 0;
	__nop();__nop();  //ȷ������д�����

	/* ���Ͷ�ȡָ�� */
	RS485_SendBuf(cmd, 8);

	/* �ȴ���Ӧ(��ʱԼ100ms) */
	timeout = 0;
	while(RS485_RxLen < 7)
	{
		DelayNuS(1000);
		timeout++;
		if(timeout > 50000)
			return 1;  //��ʱ
	}

	//�յ�����1�ֽں�,�ȴ�ʣ������(Modbus֡���3.5�ַ���3.6ms@9600)
	DelayNuS(4000);  //�ȴ�֡�������
	if(RS485_RxLen < 7)
		return 2;  //����̫��

	/* CRCУ�� */
	crc = CRC16_Modbus(RS485_RxBuf, RS485_RxLen - 2);
	if(RS485_RxBuf[RS485_RxLen - 2] != (crc & 0xFF) ||
	   RS485_RxBuf[RS485_RxLen - 1] != ((crc >> 8) & 0xFF))
		return 3;  //CRC����

	/* ��������: ��4�ֽ�=Data_H, ��5�ֽ�=Data_L */
	*weight = ((unsigned short)RS485_RxBuf[3] << 8) | RS485_RxBuf[4];

	return 0;  //�ɹ�
}

/* ===== 非阻塞称重状态机 (配合TIM3定时器) ===== */
WeightState weight_state = WEIGHT_IDLE;
static unsigned short weight_start_tick;  /* 记录发送时刻的滴答计数 */

/*
 * 启动一次重量读取: 发送Modbus读保持寄存器指令
 * 调用后状态切换为 WEIGHT_SENT
 */
void Weight_Read_Start(void)
{
    unsigned char cmd[8];
    unsigned short crc;
    unsigned char i;

    /* 仅在空闲时启动新读取, 防止重入 */
    if(weight_state != WEIGHT_IDLE)
        return;

    /* 清空接收缓冲区 */
    for(i = 0; i < RS485_RX_BUF_SIZE; i++)
        RS485_RxBuf[i] = 0;
    RS485_RxLen = 0;
    __nop();__nop();

    /* 构造Modbus读保持寄存器指令 */
    cmd[0] = 0x01;  //从机地址
    cmd[1] = 0x03;  //功能码: 读保持寄存器
    cmd[2] = 0x00;  //起始地址高字节
    cmd[3] = 0x00;  //起始地址低字节
    cmd[4] = 0x00;  //寄存器数量高字节
    cmd[5] = 0x02;  //寄存器数量低字节
    crc = CRC16_Modbus(cmd, 6);
    cmd[6] = crc & 0xFF;        //CRC低字节
    cmd[7] = (crc >> 8) & 0xFF; //CRC高字节

    /* 发送读取指令 */
    RS485_SendBuf(cmd, 8);

    /* 记录起始滴答, 用于超时判断 */
    weight_start_tick = g_tick_count;
    weight_state = WEIGHT_SENT;
}

/*
 * 轮询重量读取结果 (非阻塞)
 * 参数: weight - 输出重量值
 * 返回: 0=成功, 1=超时, 2=等待中(数据未到齐), 3=CRC错误
 * 成功/超时/CRC错误后状态自动复位为 WEIGHT_IDLE
 */
unsigned char Weight_Read_Poll(unsigned short *weight)
{
    unsigned short crc;
    unsigned short tick_diff;

    if(weight_state != WEIGHT_SENT)
        return 2;  /* 未在等待状态 */

    /* 超时判断: 2个滴答(200ms)无应答视为超时 */
    tick_diff = g_tick_count - weight_start_tick;
    if(tick_diff >= 2)
    {
        weight_state = WEIGHT_IDLE;
        return 1;  /* 超时 */
    }

    /* 数据未到齐 (Modbus应答至少7字节: 地址+功能码+长度+2数据+2CRC) */
    if(RS485_RxLen < 7)
        return 2;  /* 等待中 */

    /* 数据到齐, 进行CRC校验 */
    crc = CRC16_Modbus(RS485_RxBuf, RS485_RxLen - 2);
    if(RS485_RxBuf[RS485_RxLen - 2] != (crc & 0xFF) ||
       RS485_RxBuf[RS485_RxLen - 1] != ((crc >> 8) & 0xFF))
    {
        weight_state = WEIGHT_IDLE;
        return 3;  /* CRC错误 */
    }

    /* 解析重量: 第4字节=Data_H, 第5字节=Data_L */
    *weight = ((unsigned short)RS485_RxBuf[3] << 8) | RS485_RxBuf[4];

    weight_state = WEIGHT_IDLE;
    return 0;  /* 成功 */
}

/* ��ʼ��USART2 */
void USART2_int(void)
{
  USART2_Config();
}
#endif

/* =====================================================================
 * ecobin UART 协议传输层 (USART1 ↔ 橙派/Edge)
 * 基于 ecobin_uart_protocol.h，替换原有的 AA/BB/CC/DD 简单帧
 * ===================================================================== */

#define RX_QUEUE_SIZE         4
#define TX_BUF_SIZE           ECOBIN_UART_MAX_FRAME_LENGTH
#define HELLO_RETRY_MS        1000

#if defined(ECOBIN_UART_VOLATILE_CONFIG_HIL)
#define MCU_CAPABILITY_BITMAP ( \
    ECOBIN_UART_CAPABILITY_CONFIG_STAGING_COMMIT \
    | ECOBIN_UART_CAPABILITY_STATE_SNAPSHOT_SEGMENTS)
#else
#define MCU_CAPABILITY_BITMAP \
    ECOBIN_UART_CAPABILITY_STATE_SNAPSHOT_SEGMENTS
#endif

volatile uint32_t g_transport_uptime_ms = 0;

static struct {
    ecobin_uart_stream_parser_t  parser;
    struct {
        uint8_t                  data[ECOBIN_UART_MAX_FRAME_LENGTH];
        uint16_t                 length;
        ecobin_uart_frame_view_t view;
    } rx_queue[RX_QUEUE_SIZE];
    uint8_t delivered_payload[ECOBIN_UART_MAX_PAYLOAD_LENGTH];
    volatile uint8_t rx_head;
    volatile uint8_t rx_tail;
    volatile uint8_t rx_overflow;
    uint8_t tx_buf[TX_BUF_SIZE];
    ecobin_transport_state_t state;
    uint8_t  boot_id[8];
    uint32_t tx_sequence;
    uint32_t event_sequence;
    uint8_t  edge_boot_id[8];
    volatile uint8_t  pending_ack;
    volatile uint32_t pending_ack_tx_seq;
    volatile uint8_t  pending_ack_msg_type;
    uint32_t hello_sent_uptime;
    const char *firmware_identity;
    const char *firmware_version;
    uint8_t  peer_hello_seen;
    uint8_t  own_hello_accepted;
    volatile uint32_t diagnostics;
} g_transport;

#define CONFIG_DEVICE_BYTES_LENGTH \
    (ECOBIN_UART_CONFIG_DEVICE_BLOCK_PAYLOAD_MAX_LENGTH \
     - ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET)
#define CONFIG_PORT_BYTES_LENGTH \
    (ECOBIN_UART_CONFIG_PORT_BLOCK_PAYLOAD_MAX_LENGTH \
     - ECOBIN_UART_CONFIG_PORT_BLOCK_PORT_NO_OFFSET)

typedef struct {
    uint8_t application_uid[16];
    uint64_t version;
    uint8_t content_sha256[32];
    uint8_t mcu_payload_sha256[32];
} ecobin_config_identity_t;

typedef struct {
    uint8_t valid;
    ecobin_config_identity_t identity;
    uint8_t part_count;
    uint16_t received_part_bitmap;
    uint8_t device_bytes[CONFIG_DEVICE_BYTES_LENGTH];
    uint8_t port_bytes[CONFIG_PORT_BYTES_LENGTH];
} ecobin_config_staging_t;

typedef struct {
    uint8_t valid;
    ecobin_config_identity_t identity;
    uint8_t device_bytes[CONFIG_DEVICE_BYTES_LENGTH];
    uint8_t port_bytes[CONFIG_PORT_BYTES_LENGTH];
} ecobin_config_applied_t;

static ecobin_config_staging_t g_config_staging;
static ecobin_config_applied_t g_config_applied;
static uint8_t g_latest_mcu_command_uid[16];
static uint8_t g_last_config_commit_uid[16];
static uint8_t g_last_config_commit_digest[32];
static uint32_t g_last_config_result_event_sequence;
static uint8_t g_last_query_uid[16];
static uint8_t g_last_query_digest[32];
static uint8_t g_last_snapshot_uid[16];
static uint8_t g_snapshot_valid;
static uint8_t
    g_snapshot_begin[ECOBIN_UART_STATE_SNAPSHOT_BEGIN_PAYLOAD_MAX_LENGTH];
static uint8_t
    g_snapshot_port[ECOBIN_UART_STATE_SNAPSHOT_PORT_PAYLOAD_MAX_LENGTH];
static uint8_t
    g_snapshot_end[ECOBIN_UART_STATE_SNAPSHOT_END_PAYLOAD_MAX_LENGTH];

/* Blocking send raw bytes via USART1 */
static void transport_usart1_send(const uint8_t *data, uint16_t len)
{
#if defined(ECOBIN_TRANSPORT_HOST_TEST)
    ecobin_transport_host_send(data, len);
#else
    uint16_t i;
    for (i = 0; i < len; i++) {
        USART_SendData(USART1, data[i]);
        while (USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    }
#endif
}

/* Encode + send a complete frame via USART1 */
static int transport_send_frame(uint8_t msg_type, uint8_t flags,
                                 const uint8_t *payload, uint16_t payload_len)
{
    size_t out_len;
    int ret;
    ret = ecobin_uart_encode_frame(msg_type, flags, g_transport.tx_sequence,
                                    payload, payload_len,
                                    g_transport.tx_buf, sizeof(g_transport.tx_buf),
                                    &out_len);
    if (ret != 0) return ret;
    transport_usart1_send(g_transport.tx_buf, (uint16_t)out_len);
    g_transport.tx_sequence++;
    if (g_transport.tx_sequence == 0u) g_transport.tx_sequence = 1u;
    return 0;
}

/* Send ACK for a received message */
static void send_ack_frame(
    uint32_t ref_tx_seq,
    uint8_t ref_msg_type,
    uint8_t disposition)
{
    uint8_t payload[ECOBIN_UART_ACK_PAYLOAD_MAX_LENGTH];
    memset(payload, 0, sizeof(payload));
    memcpy(payload + ECOBIN_UART_ACK_SENDER_BOOT_ID_OFFSET,
           g_transport.boot_id, 8);
    memcpy(payload + ECOBIN_UART_ACK_REFERENCED_SENDER_BOOT_ID_OFFSET,
           g_transport.edge_boot_id, 8);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_ACK_REFERENCED_TX_SEQUENCE_OFFSET, ref_tx_seq);
    payload[ECOBIN_UART_ACK_REFERENCED_MESSAGE_TYPE_OFFSET] = ref_msg_type;
    payload[ECOBIN_UART_ACK_DISPOSITION_OFFSET] = disposition;
    transport_send_frame(ECOBIN_UART_MESSAGE_ACK, 0, payload,
                         ECOBIN_UART_ACK_PAYLOAD_MAX_LENGTH);
}

static void send_nack_frame(
    uint32_t ref_tx_seq,
    uint8_t ref_msg_type,
    uint16_t error_code)
{
    uint8_t payload[ECOBIN_UART_NACK_PAYLOAD_MAX_LENGTH];
    memset(payload, 0, sizeof(payload));
    memcpy(
        payload + ECOBIN_UART_NACK_SENDER_BOOT_ID_OFFSET,
        g_transport.boot_id,
        8u);
    memcpy(
        payload + ECOBIN_UART_NACK_REFERENCED_SENDER_BOOT_ID_OFFSET,
        g_transport.edge_boot_id,
        8u);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_NACK_REFERENCED_TX_SEQUENCE_OFFSET,
        ref_tx_seq);
    payload[ECOBIN_UART_NACK_REFERENCED_MESSAGE_TYPE_OFFSET] = ref_msg_type;
    ecobin_uart_write_u16_be(
        payload + ECOBIN_UART_NACK_ERROR_CODE_OFFSET,
        error_code);
    transport_send_frame(
        ECOBIN_UART_MESSAGE_NACK,
        0u,
        payload,
        ECOBIN_UART_NACK_PAYLOAD_MAX_LENGTH);
}

static int validate_rx_length(uint8_t msg_type, uint16_t len)
{
    switch (msg_type) {
    case ECOBIN_UART_MESSAGE_HELLO:
        return (len >= ECOBIN_UART_HELLO_PAYLOAD_MIN_LENGTH
                && len <= ECOBIN_UART_HELLO_PAYLOAD_MAX_LENGTH);
    case ECOBIN_UART_MESSAGE_HELLO_ACK:
        return (len >= ECOBIN_UART_HELLO_ACK_PAYLOAD_MIN_LENGTH
                && len <= ECOBIN_UART_HELLO_ACK_PAYLOAD_MAX_LENGTH);
    case ECOBIN_UART_MESSAGE_ACK:
        return (len >= ECOBIN_UART_ACK_PAYLOAD_MIN_LENGTH
                && len <= ECOBIN_UART_ACK_PAYLOAD_MAX_LENGTH);
    case ECOBIN_UART_MESSAGE_NACK:
        return (len >= ECOBIN_UART_NACK_PAYLOAD_MIN_LENGTH
                && len <= ECOBIN_UART_NACK_PAYLOAD_MAX_LENGTH);
    default:
        return 1;
    }
}

/* Frame callback — fires in ISR context. Minimal work only. */
static void frame_callback(
    const uint8_t *frame, size_t length,
    const ecobin_uart_frame_view_t *view, void *context)
{
    uint8_t next;
    uint8_t slot;
    (void)context;

    if (!validate_rx_length(view->message_type, view->payload_length)) {
        g_transport.diagnostics |= ECOBIN_UART_DIAG_SEMANTIC_REJECTED;
        return;
    }

    next = (uint8_t)((g_transport.rx_tail + 1u) % RX_QUEUE_SIZE);
    if (next == g_transport.rx_head) {
        g_transport.rx_overflow = 1u;
        return;
    }
    slot = g_transport.rx_tail;
    memcpy(g_transport.rx_queue[slot].data, frame, length);
    g_transport.rx_queue[slot].length = (uint16_t)length;
    g_transport.rx_queue[slot].view = *view;
    g_transport.rx_queue[slot].view.payload =
        g_transport.rx_queue[slot].data + 12u;
    g_transport.rx_tail = next;
}

/* Build and send HELLO frame */
static void transport_send_hello(void)
{
    uint8_t  payload[91];
    uint8_t  identity_len;
    uint8_t  version_len;
    uint16_t total_len;
    memset(payload, 0, sizeof(payload));
    payload[ECOBIN_UART_HELLO_SENDER_ROLE_OFFSET] =
        ECOBIN_UART_SENDER_ROLE_MCU;
    memcpy(payload + ECOBIN_UART_HELLO_SENDER_BOOT_ID_OFFSET, g_transport.boot_id, 8);
    payload[ECOBIN_UART_HELLO_SUPPORTED_MAJOR_OFFSET] = ECOBIN_UART_PROTOCOL_MAJOR;
    payload[ECOBIN_UART_HELLO_MINIMUM_MINOR_OFFSET]   = ECOBIN_UART_PROTOCOL_MINOR;
    payload[ECOBIN_UART_HELLO_MAXIMUM_MINOR_OFFSET]   = ECOBIN_UART_PROTOCOL_MINOR;
    payload[ECOBIN_UART_HELLO_PORT_COUNT_OFFSET]      = 1u;
    ecobin_uart_write_u64_be(
        payload + ECOBIN_UART_HELLO_CAPABILITY_BITMAP_OFFSET,
        MCU_CAPABILITY_BITMAP);
    ecobin_uart_write_u16_be(payload + ECOBIN_UART_HELLO_MAXIMUM_FRAME_LENGTH_OFFSET,
        ECOBIN_UART_MAX_FRAME_LENGTH);
    ecobin_uart_write_u16_be(payload + ECOBIN_UART_HELLO_PENDING_CRITICAL_EVENT_COUNT_OFFSET, 0u);

    identity_len = (uint8_t)strlen(g_transport.firmware_identity);
    if (identity_len > 32u) identity_len = 32u;
    version_len = (uint8_t)strlen(g_transport.firmware_version);
    if (version_len > 32u) version_len = 32u;

    total_len = ECOBIN_UART_HELLO_FIRMWARE_IDENTITY_OFFSET;
    payload[total_len++] = identity_len;
    memcpy(payload + ECOBIN_UART_HELLO_FIRMWARE_IDENTITY_OFFSET + 1u,
           g_transport.firmware_identity, identity_len);
    total_len = (uint16_t)(total_len + identity_len);
    payload[total_len++] = version_len;
    memcpy(payload + total_len, g_transport.firmware_version, version_len);
    total_len = (uint16_t)(total_len + version_len);
    transport_send_frame(ECOBIN_UART_MESSAGE_HELLO, 0, payload, total_len);
}

static int bytes_are_zero(const uint8_t *data, uint16_t length)
{
    uint16_t index;
    for (index = 0u; index < length; ++index) {
        if (data[index] != 0u) return 0;
    }
    return 1;
}

static void transport_send_hello_ack(
    const uint8_t edge_boot_id[8],
    uint64_t negotiated_capability,
    uint8_t status,
    uint16_t error_code)
{
    uint8_t payload[ECOBIN_UART_HELLO_ACK_PAYLOAD_MAX_LENGTH];
    memset(payload, 0, sizeof(payload));
    memcpy(
        payload + ECOBIN_UART_HELLO_ACK_RESPONDER_BOOT_ID_OFFSET,
        g_transport.boot_id,
        8u);
    memcpy(
        payload + ECOBIN_UART_HELLO_ACK_REFERENCED_SENDER_BOOT_ID_OFFSET,
        edge_boot_id,
        8u);
    payload[ECOBIN_UART_HELLO_ACK_SELECTED_MAJOR_OFFSET] =
        ECOBIN_UART_PROTOCOL_MAJOR;
    payload[ECOBIN_UART_HELLO_ACK_SELECTED_MINOR_OFFSET] =
        ECOBIN_UART_PROTOCOL_MINOR;
    payload[ECOBIN_UART_HELLO_ACK_STATUS_OFFSET] = status;
    payload[ECOBIN_UART_HELLO_ACK_PORT_COUNT_OFFSET] = 1u;
    ecobin_uart_write_u64_be(
        payload + ECOBIN_UART_HELLO_ACK_CAPABILITY_BITMAP_OFFSET,
        negotiated_capability);
    ecobin_uart_write_u16_be(
        payload + ECOBIN_UART_HELLO_ACK_MAXIMUM_FRAME_LENGTH_OFFSET,
        ECOBIN_UART_MAX_FRAME_LENGTH);
    ecobin_uart_write_u16_be(
        payload + ECOBIN_UART_HELLO_ACK_ERROR_CODE_OFFSET,
        error_code);
    transport_send_frame(
        ECOBIN_UART_MESSAGE_HELLO_ACK,
        0u,
        payload,
        ECOBIN_UART_HELLO_ACK_PAYLOAD_MAX_LENGTH);
}

static void handle_edge_hello(const ecobin_uart_frame_view_t *view)
{
    const uint8_t *payload;
    uint8_t identity_length;
    uint8_t version_length;
    uint16_t version_length_offset;
    uint64_t edge_capability;
    uint64_t negotiated_capability;
    uint8_t status;
    uint16_t error_code;

    payload = view->payload;
    status = ECOBIN_UART_HELLO_STATUS_ACCEPTED;
    error_code = ECOBIN_UART_NACK_ERROR_NONE;
    edge_capability = ecobin_uart_read_u64_be(
        payload + ECOBIN_UART_HELLO_CAPABILITY_BITMAP_OFFSET);
    negotiated_capability = edge_capability & MCU_CAPABILITY_BITMAP;

    identity_length = payload[ECOBIN_UART_HELLO_FIRMWARE_IDENTITY_OFFSET];
    version_length_offset = (uint16_t)(
        ECOBIN_UART_HELLO_FIRMWARE_IDENTITY_OFFSET + 1u + identity_length);
    if (version_length_offset >= view->payload_length) {
        status = ECOBIN_UART_HELLO_STATUS_INCOMPATIBLE;
        error_code = ECOBIN_UART_NACK_ERROR_INVALID_LENGTH;
    } else {
        version_length = payload[version_length_offset];
        if ((uint16_t)(version_length_offset + 1u + version_length)
            != view->payload_length) {
            status = ECOBIN_UART_HELLO_STATUS_INCOMPATIBLE;
            error_code = ECOBIN_UART_NACK_ERROR_INVALID_LENGTH;
        }
    }

    if (payload[ECOBIN_UART_HELLO_SENDER_ROLE_OFFSET]
            != ECOBIN_UART_SENDER_ROLE_EDGE
        || bytes_are_zero(
            payload + ECOBIN_UART_HELLO_SENDER_BOOT_ID_OFFSET, 8u)
        || payload[ECOBIN_UART_HELLO_PORT_COUNT_OFFSET] != 1u
        || (edge_capability & ~UINT64_C(0x1FFF)) != 0u
        || (edge_capability & MCU_CAPABILITY_BITMAP) != MCU_CAPABILITY_BITMAP) {
        status = ECOBIN_UART_HELLO_STATUS_INCOMPATIBLE;
        error_code = ECOBIN_UART_NACK_ERROR_INVALID_FIELD;
    }
    if (payload[ECOBIN_UART_HELLO_SUPPORTED_MAJOR_OFFSET]
            != ECOBIN_UART_PROTOCOL_MAJOR
        || payload[ECOBIN_UART_HELLO_MINIMUM_MINOR_OFFSET]
            > ECOBIN_UART_PROTOCOL_MINOR
#if ECOBIN_UART_PROTOCOL_MINOR > 0u
        || payload[ECOBIN_UART_HELLO_MAXIMUM_MINOR_OFFSET]
            < ECOBIN_UART_PROTOCOL_MINOR
#endif
        || ecobin_uart_read_u16_be(
            payload + ECOBIN_UART_HELLO_MAXIMUM_FRAME_LENGTH_OFFSET)
            < ECOBIN_UART_MAX_FRAME_LENGTH) {
        status = ECOBIN_UART_HELLO_STATUS_INCOMPATIBLE;
        error_code = ECOBIN_UART_NACK_ERROR_UNSUPPORTED_VERSION;
    }

    if (memcmp(
            g_transport.edge_boot_id,
            payload + ECOBIN_UART_HELLO_SENDER_BOOT_ID_OFFSET,
            8u) != 0) {
        g_transport.own_hello_accepted = 0u;
    }
    memcpy(
        g_transport.edge_boot_id,
        payload + ECOBIN_UART_HELLO_SENDER_BOOT_ID_OFFSET,
        8u);

    /*
     * Send our HELLO before HELLO_ACK so an Edge process that opened after
     * the MCU startup retry window still observes the symmetric handshake in
     * deterministic order.
     */
    transport_send_hello();
    transport_send_hello_ack(
        g_transport.edge_boot_id,
        negotiated_capability,
        status,
        error_code);
    g_transport.peer_hello_seen =
        (status == ECOBIN_UART_HELLO_STATUS_ACCEPTED) ? 1u : 0u;
    if (g_transport.peer_hello_seen != 0u
        && g_transport.own_hello_accepted != 0u) {
        g_transport.state = ECOBIN_TRANSPORT_READY;
    } else {
        g_transport.state = ECOBIN_TRANSPORT_HELLO_SENT;
    }
}

static void handle_edge_hello_ack(const ecobin_uart_frame_view_t *view)
{
    const uint8_t *payload;
    uint64_t negotiated_capability;

    payload = view->payload;
    negotiated_capability = ecobin_uart_read_u64_be(
        payload + ECOBIN_UART_HELLO_ACK_CAPABILITY_BITMAP_OFFSET);
    if (memcmp(
            payload + ECOBIN_UART_HELLO_ACK_REFERENCED_SENDER_BOOT_ID_OFFSET,
            g_transport.boot_id,
            8u) != 0
        || payload[ECOBIN_UART_HELLO_ACK_SELECTED_MAJOR_OFFSET]
            != ECOBIN_UART_PROTOCOL_MAJOR
        || payload[ECOBIN_UART_HELLO_ACK_SELECTED_MINOR_OFFSET]
            != ECOBIN_UART_PROTOCOL_MINOR
        || payload[ECOBIN_UART_HELLO_ACK_STATUS_OFFSET]
            != ECOBIN_UART_HELLO_STATUS_ACCEPTED
        || payload[ECOBIN_UART_HELLO_ACK_PORT_COUNT_OFFSET] != 1u
        || negotiated_capability != MCU_CAPABILITY_BITMAP
        || ecobin_uart_read_u16_be(
            payload + ECOBIN_UART_HELLO_ACK_MAXIMUM_FRAME_LENGTH_OFFSET)
            < ECOBIN_UART_MAX_FRAME_LENGTH
        || ecobin_uart_read_u16_be(
            payload + ECOBIN_UART_HELLO_ACK_ERROR_CODE_OFFSET)
            != ECOBIN_UART_NACK_ERROR_NONE) {
        g_transport.own_hello_accepted = 0u;
        g_transport.state = ECOBIN_TRANSPORT_HELLO_SENT;
        return;
    }
    memcpy(
        g_transport.edge_boot_id,
        payload + ECOBIN_UART_HELLO_ACK_RESPONDER_BOOT_ID_OFFSET,
        8u);
    g_transport.own_hello_accepted = 1u;
    if (g_transport.peer_hello_seen != 0u) {
        g_transport.state = ECOBIN_TRANSPORT_READY;
    }
}

static int command_digest_is_valid(
    uint8_t message_type,
    const uint8_t *payload,
    uint16_t payload_length)
{
    static const uint8_t domain[] = {
        0x45u, 0x43u, 0x4Fu, 0x42u, 0x49u, 0x4Eu, 0x3Au, 0x55u,
        0x41u, 0x52u, 0x54u, 0x3Au, 0x43u, 0x4Fu, 0x4Du, 0x4Du,
        0x41u, 0x4Eu, 0x44u, 0x3Au, 0x76u, 0x31u, 0x00u
    };
    ecobin_uart_sha256_context_t context;
    uint8_t semantic_length[2];
    uint8_t actual[32];
    uint16_t semantic_size;

    if (payload_length < 48u || bytes_are_zero(payload, 16u)) return 0;
    semantic_size = (uint16_t)(payload_length - 48u);
    ecobin_uart_write_u16_be(semantic_length, semantic_size);
    ecobin_uart_sha256_init(&context);
    ecobin_uart_sha256_update(&context, domain, sizeof(domain));
    ecobin_uart_sha256_update(&context, &message_type, 1u);
    ecobin_uart_sha256_update(&context, semantic_length, 2u);
    ecobin_uart_sha256_update(&context, payload + 48u, semantic_size);
    ecobin_uart_sha256_final(&context, actual);
    return memcmp(actual, payload + 16u, 32u) == 0;
}

static void config_identity_from_payload(
    ecobin_config_identity_t *identity,
    const uint8_t *payload)
{
    memcpy(
        identity->application_uid,
        payload + ECOBIN_UART_CONFIG_BEGIN_APPLICATION_UID_OFFSET,
        16u);
    identity->version = ecobin_uart_read_u64_be(
        payload + ECOBIN_UART_CONFIG_BEGIN_CONFIG_VERSION_OFFSET);
    memcpy(
        identity->content_sha256,
        payload + ECOBIN_UART_CONFIG_BEGIN_CONTENT_SHA256_OFFSET,
        32u);
    memcpy(
        identity->mcu_payload_sha256,
        payload + ECOBIN_UART_CONFIG_BEGIN_MCU_PAYLOAD_SHA256_OFFSET,
        32u);
}

static int config_identity_is_valid(const ecobin_config_identity_t *identity)
{
    return !bytes_are_zero(identity->application_uid, 16u)
        && identity->version > 0u
        && identity->version <= UINT64_C(9007199254740991);
}

static int config_identity_equals(
    const ecobin_config_identity_t *left,
    const ecobin_config_identity_t *right)
{
    return left->version == right->version
        && memcmp(left->application_uid, right->application_uid, 16u) == 0
        && memcmp(left->content_sha256, right->content_sha256, 32u) == 0
        && memcmp(
            left->mcu_payload_sha256,
            right->mcu_payload_sha256,
            32u) == 0;
}

static int config_device_fields_are_valid(const uint8_t *payload)
{
    return ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET)
               >= 1000u
        && ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_DEVICE_BLOCK_NEGATIVE_WEIGHT_THRESHOLD_GRAMS_OFFSET)
               > 0u
        && ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_DEVICE_BLOCK_DELIVERY_AUTO_CLOSE_MS_OFFSET)
               >= 1000u
        && ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_DEVICE_BLOCK_WEIGHT_MEASUREMENT_TIMEOUT_MS_OFFSET)
               > 0u
        && ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_DEVICE_BLOCK_CLEAN_SOLENOID_PULSE_MS_OFFSET)
               > 0u
        && payload[
               ECOBIN_UART_CONFIG_DEVICE_BLOCK_SMOKE_MONITORING_ENABLED_OFFSET]
               <= 1u;
}

static int config_port_fields_are_valid(const uint8_t *payload)
{
    int32_t minimum_weight;
    int32_t maximum_weight;
    minimum_weight = ecobin_uart_read_i32_be(
        payload + ECOBIN_UART_CONFIG_PORT_BLOCK_WEIGHT_MINIMUM_GRAMS_OFFSET);
    maximum_weight = ecobin_uart_read_i32_be(
        payload + ECOBIN_UART_CONFIG_PORT_BLOCK_WEIGHT_MAXIMUM_GRAMS_OFFSET);
    return payload[ECOBIN_UART_CONFIG_PORT_BLOCK_PORT_NO_OFFSET] == 1u
        && payload[ECOBIN_UART_CONFIG_PORT_BLOCK_ENABLED_OFFSET] <= 1u
        && payload[ECOBIN_UART_CONFIG_PORT_BLOCK_FULLNESS_MODE_OFFSET] >= 1u
        && payload[ECOBIN_UART_CONFIG_PORT_BLOCK_FULLNESS_MODE_OFFSET] <= 3u
        && ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_PORT_BLOCK_CONFIGURED_FULL_WEIGHT_GRAMS_OFFSET)
               > 0u
        && ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_PORT_BLOCK_FULLNESS_SETTLE_WAIT_MS_OFFSET)
               > 0u
        && ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_PORT_BLOCK_FULLNESS_CONFIRMATION_WAIT_MS_OFFSET)
               > 0u
        && ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_PORT_BLOCK_WEIGHT_STABLE_WINDOW_MS_OFFSET)
               > 0u
        && ecobin_uart_read_u16_be(
               payload
               + ECOBIN_UART_CONFIG_PORT_BLOCK_WEIGHT_REQUIRED_SAMPLE_COUNT_OFFSET)
               > 0u
        && ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_PORT_BLOCK_WEIGHT_MEASUREMENT_TIMEOUT_MS_OFFSET)
               > 0u
        && minimum_weight < maximum_weight
        && ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_PORT_BLOCK_CALIBRATION_VERSION_OFFSET)
               > 0u
        && ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_PORT_BLOCK_INFRARED_SAMPLE_TIMEOUT_MS_OFFSET)
               > 0u
        && ecobin_uart_read_u32_be(
               payload
               + ECOBIN_UART_CONFIG_PORT_BLOCK_DELIVERY_DOOR_OPERATION_TIMEOUT_MS_OFFSET)
               > 0u;
}

static void compute_staging_mcu_payload_sha256(uint8_t result[32])
{
    static const uint8_t domain[] = {
        0x45u, 0x43u, 0x4Fu, 0x42u, 0x49u, 0x4Eu, 0x3Au, 0x55u,
        0x41u, 0x52u, 0x54u, 0x3Au, 0x4Du, 0x43u, 0x55u, 0x2Du,
        0x43u, 0x4Fu, 0x4Eu, 0x46u, 0x49u, 0x47u, 0x3Au, 0x76u,
        0x31u, 0x00u
    };
    ecobin_uart_sha256_context_t context;
    uint8_t version[8];
    uint8_t port_count;

    ecobin_uart_write_u64_be(version, g_config_staging.identity.version);
    port_count = 1u;
    ecobin_uart_sha256_init(&context);
    ecobin_uart_sha256_update(&context, domain, sizeof(domain));
    ecobin_uart_sha256_update(&context, version, sizeof(version));
    ecobin_uart_sha256_update(
        &context,
        g_config_staging.identity.content_sha256,
        32u);
    ecobin_uart_sha256_update(&context, &port_count, 1u);
    ecobin_uart_sha256_update(
        &context,
        g_config_staging.device_bytes,
        CONFIG_DEVICE_BYTES_LENGTH);
    ecobin_uart_sha256_update(
        &context,
        g_config_staging.port_bytes,
        CONFIG_PORT_BYTES_LENGTH);
    ecobin_uart_sha256_final(&context, result);
}

static void send_config_apply_result(
    const uint8_t commit_command_uid[16],
    uint32_t event_sequence)
{
    uint8_t payload[ECOBIN_UART_CONFIG_APPLY_RESULT_PAYLOAD_MAX_LENGTH];
    memset(payload, 0, sizeof(payload));
    memcpy(
        payload + ECOBIN_UART_CONFIG_APPLY_RESULT_MCU_BOOT_ID_OFFSET,
        g_transport.boot_id,
        8u);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_CONFIG_APPLY_RESULT_MCU_EVENT_SEQUENCE_OFFSET,
        event_sequence);
    ecobin_uart_write_u64_be(
        payload + ECOBIN_UART_CONFIG_APPLY_RESULT_UPTIME_MS_OFFSET,
        (uint64_t)g_transport_uptime_ms);
    memcpy(
        payload + ECOBIN_UART_CONFIG_APPLY_RESULT_MCU_COMMAND_UID_OFFSET,
        commit_command_uid,
        16u);
    memcpy(
        payload + ECOBIN_UART_CONFIG_APPLY_RESULT_APPLICATION_UID_OFFSET,
        g_config_applied.identity.application_uid,
        16u);
    payload[ECOBIN_UART_CONFIG_APPLY_RESULT_STATUS_OFFSET] =
        ECOBIN_UART_CONFIG_APPLY_STATUS_APPLIED;
    ecobin_uart_write_u64_be(
        payload + ECOBIN_UART_CONFIG_APPLY_RESULT_CONFIG_VERSION_OFFSET,
        g_config_applied.identity.version);
    memcpy(
        payload + ECOBIN_UART_CONFIG_APPLY_RESULT_CONTENT_SHA256_OFFSET,
        g_config_applied.identity.content_sha256,
        32u);
    memcpy(
        payload + ECOBIN_UART_CONFIG_APPLY_RESULT_MCU_PAYLOAD_SHA256_OFFSET,
        g_config_applied.identity.mcu_payload_sha256,
        32u);
    ecobin_uart_write_u16_be(
        payload + ECOBIN_UART_CONFIG_APPLY_RESULT_FAULT_CODE_OFFSET,
        ECOBIN_UART_FAULT_CODE_NONE);
    transport_send_frame(
        ECOBIN_UART_MESSAGE_CONFIG_APPLY_RESULT,
        ECOBIN_UART_FLAG_ACK_REQUIRED,
        payload,
        ECOBIN_UART_CONFIG_APPLY_RESULT_PAYLOAD_MAX_LENGTH);
}

static int handle_config_command(const ecobin_uart_frame_view_t *view)
{
    ecobin_config_identity_t identity;
    uint8_t part_index;
    uint8_t part_count;
    uint8_t computed_mcu_sha256[32];

    if (view->message_type < ECOBIN_UART_MESSAGE_CONFIG_BEGIN
        || view->message_type > ECOBIN_UART_MESSAGE_CONFIG_COMMIT) {
        return 0;
    }
    if (!command_digest_is_valid(
            view->message_type,
            view->payload,
            view->payload_length)) {
        send_nack_frame(
            view->tx_sequence,
            view->message_type,
            ECOBIN_UART_NACK_ERROR_INVALID_FIELD);
        return 1;
    }

    config_identity_from_payload(&identity, view->payload);
    part_index = view->payload[136u];
    part_count = view->payload[137u];
    if (!config_identity_is_valid(&identity) || part_count != 4u) {
        send_nack_frame(
            view->tx_sequence,
            view->message_type,
            ECOBIN_UART_NACK_ERROR_INVALID_FIELD);
        return 1;
    }

    if (view->message_type == ECOBIN_UART_MESSAGE_CONFIG_COMMIT
        && g_config_applied.valid != 0u
        && memcmp(g_last_config_commit_uid, view->payload, 16u) == 0
        && memcmp(
            g_last_config_commit_digest,
            view->payload + 16u,
            32u) == 0
        && config_identity_equals(&identity, &g_config_applied.identity)) {
        if (g_config_staging.valid != 0u
            && config_identity_equals(
                &g_config_staging.identity,
                &g_config_applied.identity)) {
            memset(&g_config_staging, 0, sizeof(g_config_staging));
        }
        send_ack_frame(
            view->tx_sequence,
            view->message_type,
            ECOBIN_UART_ACK_DISPOSITION_DUPLICATE_ACCEPTED);
        send_config_apply_result(
            g_last_config_commit_uid,
            g_last_config_result_event_sequence);
        return 1;
    }

    if (view->message_type == ECOBIN_UART_MESSAGE_CONFIG_BEGIN) {
        if (view->payload_length
                != ECOBIN_UART_CONFIG_BEGIN_PAYLOAD_MAX_LENGTH
            || part_index != 1u
            || view->payload[
                ECOBIN_UART_CONFIG_BEGIN_EXPECTED_PORT_COUNT_OFFSET] != 1u) {
            send_nack_frame(
                view->tx_sequence,
                view->message_type,
                ECOBIN_UART_NACK_ERROR_INVALID_FIELD);
            return 1;
        }
        memset(&g_config_staging, 0, sizeof(g_config_staging));
        g_config_staging.valid = 1u;
        g_config_staging.identity = identity;
        g_config_staging.part_count = part_count;
        g_config_staging.received_part_bitmap = 1u;
    } else {
        if (g_config_staging.valid == 0u
            || !config_identity_equals(&identity, &g_config_staging.identity)) {
            send_nack_frame(
                view->tx_sequence,
                view->message_type,
                ECOBIN_UART_NACK_ERROR_STATE_CONFLICT);
            return 1;
        }
        if (view->message_type == ECOBIN_UART_MESSAGE_CONFIG_DEVICE_BLOCK) {
            if (view->payload_length
                    != ECOBIN_UART_CONFIG_DEVICE_BLOCK_PAYLOAD_MAX_LENGTH
                || part_index != 2u
                || !config_device_fields_are_valid(view->payload)) {
                send_nack_frame(
                    view->tx_sequence,
                    view->message_type,
                    ECOBIN_UART_NACK_ERROR_INVALID_FIELD);
                return 1;
            }
            memcpy(
                g_config_staging.device_bytes,
                view->payload
                    + ECOBIN_UART_CONFIG_DEVICE_BLOCK_CONTINUE_DELIVERY_WAIT_MS_OFFSET,
                CONFIG_DEVICE_BYTES_LENGTH);
            g_config_staging.received_part_bitmap |= 2u;
        } else if (view->message_type
                   == ECOBIN_UART_MESSAGE_CONFIG_PORT_BLOCK) {
            if (view->payload_length
                    != ECOBIN_UART_CONFIG_PORT_BLOCK_PAYLOAD_MAX_LENGTH
                || part_index != 3u
                || !config_port_fields_are_valid(view->payload)) {
                send_nack_frame(
                    view->tx_sequence,
                    view->message_type,
                    ECOBIN_UART_NACK_ERROR_INVALID_FIELD);
                return 1;
            }
            memcpy(
                g_config_staging.port_bytes,
                view->payload + ECOBIN_UART_CONFIG_PORT_BLOCK_PORT_NO_OFFSET,
                CONFIG_PORT_BYTES_LENGTH);
            g_config_staging.received_part_bitmap |= 4u;
        } else {
            if (view->payload_length
                    != ECOBIN_UART_CONFIG_COMMIT_PAYLOAD_MAX_LENGTH
                || part_index != 4u
                || g_config_staging.received_part_bitmap != 7u) {
                send_nack_frame(
                    view->tx_sequence,
                    view->message_type,
                    ECOBIN_UART_NACK_ERROR_STATE_CONFLICT);
                return 1;
            }
            compute_staging_mcu_payload_sha256(computed_mcu_sha256);
            if (memcmp(
                    computed_mcu_sha256,
                    g_config_staging.identity.mcu_payload_sha256,
                    32u) != 0) {
                send_nack_frame(
                    view->tx_sequence,
                    view->message_type,
                    ECOBIN_UART_NACK_ERROR_INVALID_FIELD);
                return 1;
            }
            memset(&g_config_applied, 0, sizeof(g_config_applied));
            g_config_applied.valid = 1u;
            g_config_applied.identity = g_config_staging.identity;
            memcpy(
                g_config_applied.device_bytes,
                g_config_staging.device_bytes,
                CONFIG_DEVICE_BYTES_LENGTH);
            memcpy(
                g_config_applied.port_bytes,
                g_config_staging.port_bytes,
                CONFIG_PORT_BYTES_LENGTH);
            memcpy(g_last_config_commit_uid, view->payload, 16u);
            memcpy(
                g_last_config_commit_digest,
                view->payload + 16u,
                32u);
            g_transport.event_sequence++;
            g_last_config_result_event_sequence =
                g_transport.event_sequence;
            memset(&g_config_staging, 0, sizeof(g_config_staging));
        }
    }

    memcpy(g_latest_mcu_command_uid, view->payload, 16u);
    send_ack_frame(
        view->tx_sequence,
        view->message_type,
        ECOBIN_UART_ACK_DISPOSITION_ACCEPTED);
    if (view->message_type == ECOBIN_UART_MESSAGE_CONFIG_COMMIT) {
        send_config_apply_result(
            g_last_config_commit_uid,
            g_last_config_result_event_sequence);
    }
    return 1;
}

static void compute_snapshot_sha256(uint8_t result[32])
{
    static const uint8_t domain[] = {
        0x45u, 0x43u, 0x4Fu, 0x42u, 0x49u, 0x4Eu, 0x3Au, 0x55u,
        0x41u, 0x52u, 0x54u, 0x3Au, 0x53u, 0x4Eu, 0x41u, 0x50u,
        0x53u, 0x48u, 0x4Fu, 0x54u, 0x3Au, 0x76u, 0x31u, 0x00u
    };
    ecobin_uart_sha256_context_t context;
    ecobin_uart_sha256_init(&context);
    ecobin_uart_sha256_update(&context, domain, sizeof(domain));
    ecobin_uart_sha256_update(
        &context,
        g_snapshot_begin
            + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_SNAPSHOT_UID_OFFSET,
        ECOBIN_UART_STATE_SNAPSHOT_BEGIN_PART_INDEX_OFFSET
            - ECOBIN_UART_STATE_SNAPSHOT_BEGIN_SNAPSHOT_UID_OFFSET);
    ecobin_uart_sha256_update(
        &context,
        g_snapshot_begin
            + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_RESET_REASON_OFFSET,
        1u);
    ecobin_uart_sha256_update(
        &context,
        g_snapshot_port + ECOBIN_UART_STATE_SNAPSHOT_PORT_PORT_NO_OFFSET,
        ECOBIN_UART_STATE_SNAPSHOT_PORT_PAYLOAD_MAX_LENGTH
            - ECOBIN_UART_STATE_SNAPSHOT_PORT_PORT_NO_OFFSET);
    ecobin_uart_sha256_update(
        &context,
        g_snapshot_end
            + ECOBIN_UART_STATE_SNAPSHOT_END_PENDING_CRITICAL_EVENT_COUNT_OFFSET,
        ECOBIN_UART_STATE_SNAPSHOT_END_SNAPSHOT_SHA256_OFFSET
            - ECOBIN_UART_STATE_SNAPSHOT_END_PENDING_CRITICAL_EVENT_COUNT_OFFSET);
    ecobin_uart_sha256_final(&context, result);
}

static void build_state_snapshot(
    const uint8_t query_command_uid[16],
    const uint8_t snapshot_uid[16])
{
    uint8_t snapshot_sha256[32];
    memset(g_snapshot_begin, 0, sizeof(g_snapshot_begin));
    memset(g_snapshot_port, 0, sizeof(g_snapshot_port));
    memset(g_snapshot_end, 0, sizeof(g_snapshot_end));

    g_transport.event_sequence++;
    memcpy(
        g_snapshot_begin
            + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_MCU_BOOT_ID_OFFSET,
        g_transport.boot_id,
        8u);
    ecobin_uart_write_u32_be(
        g_snapshot_begin
            + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_MCU_EVENT_SEQUENCE_OFFSET,
        g_transport.event_sequence);
    ecobin_uart_write_u64_be(
        g_snapshot_begin + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_UPTIME_MS_OFFSET,
        (uint64_t)g_transport_uptime_ms);
    memcpy(
        g_snapshot_begin
            + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_SNAPSHOT_UID_OFFSET,
        snapshot_uid,
        16u);
    memcpy(
        g_snapshot_begin
            + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_QUERY_COMMAND_UID_OFFSET,
        query_command_uid,
        16u);
    g_snapshot_begin[
        ECOBIN_UART_STATE_SNAPSHOT_BEGIN_PROTOCOL_MAJOR_OFFSET] =
        ECOBIN_UART_PROTOCOL_MAJOR;
    g_snapshot_begin[
        ECOBIN_UART_STATE_SNAPSHOT_BEGIN_PROTOCOL_MINOR_OFFSET] =
        ECOBIN_UART_PROTOCOL_MINOR;
    ecobin_uart_write_u32_be(
        g_snapshot_begin
            + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_FIRMWARE_VERSION_CODE_OFFSET,
        10000u);
    g_snapshot_begin[
        ECOBIN_UART_STATE_SNAPSHOT_BEGIN_ACTIVE_WORK_TYPE_OFFSET] =
        ECOBIN_UART_WORK_TYPE_NONE;
    g_snapshot_begin[
        ECOBIN_UART_STATE_SNAPSHOT_BEGIN_ACTIVE_PORT_NO_OFFSET] = 0u;
    g_snapshot_begin[
        ECOBIN_UART_STATE_SNAPSHOT_BEGIN_ACTIVE_WORK_PHASE_OFFSET] =
        ECOBIN_UART_MCU_WORK_PHASE_IDLE;
    memcpy(
        g_snapshot_begin
            + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_LATEST_MCU_COMMAND_UID_OFFSET,
        g_latest_mcu_command_uid,
        16u);
    if (g_config_applied.valid != 0u) {
        ecobin_uart_write_u64_be(
            g_snapshot_begin
                + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_APPLIED_CONFIG_VERSION_OFFSET,
            g_config_applied.identity.version);
        memcpy(
            g_snapshot_begin
                + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_APPLIED_CONTENT_SHA256_OFFSET,
            g_config_applied.identity.content_sha256,
            32u);
        memcpy(
            g_snapshot_begin
                + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_APPLIED_MCU_PAYLOAD_SHA256_OFFSET,
            g_config_applied.identity.mcu_payload_sha256,
            32u);
    }
    if (g_config_staging.valid != 0u) {
        g_snapshot_begin[
            ECOBIN_UART_STATE_SNAPSHOT_BEGIN_STAGING_VALID_OFFSET] = 1u;
        memcpy(
            g_snapshot_begin
                + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_STAGING_APPLICATION_UID_OFFSET,
            g_config_staging.identity.application_uid,
            16u);
        ecobin_uart_write_u64_be(
            g_snapshot_begin
                + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_STAGING_CONFIG_VERSION_OFFSET,
            g_config_staging.identity.version);
        memcpy(
            g_snapshot_begin
                + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_STAGING_MCU_PAYLOAD_SHA256_OFFSET,
            g_config_staging.identity.mcu_payload_sha256,
            32u);
        g_snapshot_begin[
            ECOBIN_UART_STATE_SNAPSHOT_BEGIN_STAGING_PART_COUNT_OFFSET] =
            g_config_staging.part_count;
        ecobin_uart_write_u16_be(
            g_snapshot_begin
                + ECOBIN_UART_STATE_SNAPSHOT_BEGIN_STAGING_RECEIVED_PART_BITMAP_OFFSET,
            g_config_staging.received_part_bitmap);
    }
    g_snapshot_begin[ECOBIN_UART_STATE_SNAPSHOT_BEGIN_PORT_COUNT_OFFSET] = 1u;
    g_snapshot_begin[ECOBIN_UART_STATE_SNAPSHOT_BEGIN_PART_INDEX_OFFSET] = 1u;
    g_snapshot_begin[ECOBIN_UART_STATE_SNAPSHOT_BEGIN_PART_COUNT_OFFSET] = 3u;
    g_snapshot_begin[ECOBIN_UART_STATE_SNAPSHOT_BEGIN_RESET_REASON_OFFSET] =
        ECOBIN_UART_RESET_REASON_POWER_ON;

    g_transport.event_sequence++;
    memcpy(
        g_snapshot_port + ECOBIN_UART_STATE_SNAPSHOT_PORT_MCU_BOOT_ID_OFFSET,
        g_transport.boot_id,
        8u);
    ecobin_uart_write_u32_be(
        g_snapshot_port
            + ECOBIN_UART_STATE_SNAPSHOT_PORT_MCU_EVENT_SEQUENCE_OFFSET,
        g_transport.event_sequence);
    ecobin_uart_write_u64_be(
        g_snapshot_port + ECOBIN_UART_STATE_SNAPSHOT_PORT_UPTIME_MS_OFFSET,
        (uint64_t)g_transport_uptime_ms);
    memcpy(
        g_snapshot_port + ECOBIN_UART_STATE_SNAPSHOT_PORT_SNAPSHOT_UID_OFFSET,
        snapshot_uid,
        16u);
    g_snapshot_port[ECOBIN_UART_STATE_SNAPSHOT_PORT_PART_INDEX_OFFSET] = 2u;
    g_snapshot_port[ECOBIN_UART_STATE_SNAPSHOT_PORT_PART_COUNT_OFFSET] = 3u;
    g_snapshot_port[ECOBIN_UART_STATE_SNAPSHOT_PORT_PORT_NO_OFFSET] = 1u;
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_DELIVERY_DOOR_STATE_OFFSET] =
        ECOBIN_UART_DELIVERY_DOOR_STATE_UNKNOWN;
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_DELIVERY_DOOR_HEALTH_OFFSET] =
        ECOBIN_UART_DELIVERY_DOOR_HEALTH_SWITCH_FAULT;
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_CLEAN_LOCK_POWER_STATE_OFFSET] =
        ECOBIN_UART_CLEAN_LOCK_POWER_STATE_DEENERGIZED;
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_CLEAN_SOLENOID_HEALTH_OFFSET] =
        ECOBIN_UART_SOLENOID_HEALTH_OK;
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_INFERRED_CLEAN_DOOR_STATE_OFFSET] =
        ECOBIN_UART_INFERRED_CLEAN_DOOR_STATE_CLOSED;
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_CLEAN_DOOR_STATE_BASIS_OFFSET] =
        ECOBIN_UART_CLEAN_DOOR_STATE_BASIS_INFERRED_FROM_LOCK_POWER;
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_CLEANER_PHYSICAL_CLOSE_CONFIRMED_OFFSET]
        = 0u;
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_MEASUREMENT_STATUS_OFFSET] =
        ECOBIN_UART_MEASUREMENT_STATUS_SENSOR_FAULT;
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_STABLE_WEIGHT_VALID_OFFSET] = 0u;
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_LAST_OBSERVED_WEIGHT_VALID_OFFSET] = 0u;
    if (g_config_applied.valid != 0u) {
        memcpy(
            g_snapshot_port
                + ECOBIN_UART_STATE_SNAPSHOT_PORT_CALIBRATION_VERSION_OFFSET,
            g_config_applied.port_bytes
                + (ECOBIN_UART_CONFIG_PORT_BLOCK_CALIBRATION_VERSION_OFFSET
                   - ECOBIN_UART_CONFIG_PORT_BLOCK_PORT_NO_OFFSET),
            4u);
    }
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_WEIGHT_SENSOR_HEALTH_OFFSET] =
        ECOBIN_UART_SENSOR_HEALTH_UNKNOWN;
    ecobin_uart_write_u16_be(
        g_snapshot_port + ECOBIN_UART_STATE_SNAPSHOT_PORT_FAULT_CODE_OFFSET,
        ECOBIN_UART_FAULT_CODE_WEIGHT_SENSOR);
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_INFRARED_VALUE_OFFSET] =
        ECOBIN_UART_INFRARED_VALUE_UNKNOWN;
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_INFRARED_HEALTH_OFFSET] =
        ECOBIN_UART_SENSOR_HEALTH_UNKNOWN;
    g_snapshot_port[ECOBIN_UART_STATE_SNAPSHOT_PORT_SMOKE_STATE_OFFSET] =
        ECOBIN_UART_SMOKE_STATE_UNKNOWN;
    g_snapshot_port[
        ECOBIN_UART_STATE_SNAPSHOT_PORT_SMOKE_SENSOR_HEALTH_OFFSET] =
        ECOBIN_UART_SENSOR_HEALTH_UNKNOWN;

    g_transport.event_sequence++;
    memcpy(
        g_snapshot_end + ECOBIN_UART_STATE_SNAPSHOT_END_MCU_BOOT_ID_OFFSET,
        g_transport.boot_id,
        8u);
    ecobin_uart_write_u32_be(
        g_snapshot_end
            + ECOBIN_UART_STATE_SNAPSHOT_END_MCU_EVENT_SEQUENCE_OFFSET,
        g_transport.event_sequence);
    ecobin_uart_write_u64_be(
        g_snapshot_end + ECOBIN_UART_STATE_SNAPSHOT_END_UPTIME_MS_OFFSET,
        (uint64_t)g_transport_uptime_ms);
    memcpy(
        g_snapshot_end + ECOBIN_UART_STATE_SNAPSHOT_END_SNAPSHOT_UID_OFFSET,
        snapshot_uid,
        16u);
    g_snapshot_end[ECOBIN_UART_STATE_SNAPSHOT_END_PART_INDEX_OFFSET] = 3u;
    g_snapshot_end[ECOBIN_UART_STATE_SNAPSHOT_END_PART_COUNT_OFFSET] = 3u;
    compute_snapshot_sha256(snapshot_sha256);
    memcpy(
        g_snapshot_end + ECOBIN_UART_STATE_SNAPSHOT_END_SNAPSHOT_SHA256_OFFSET,
        snapshot_sha256,
        32u);
    g_snapshot_valid = 1u;
}

static void send_stored_state_snapshot(void)
{
    transport_send_frame(
        ECOBIN_UART_MESSAGE_STATE_SNAPSHOT_BEGIN,
        ECOBIN_UART_FLAG_ACK_REQUIRED,
        g_snapshot_begin,
        ECOBIN_UART_STATE_SNAPSHOT_BEGIN_PAYLOAD_MAX_LENGTH);
    transport_send_frame(
        ECOBIN_UART_MESSAGE_STATE_SNAPSHOT_PORT,
        ECOBIN_UART_FLAG_ACK_REQUIRED,
        g_snapshot_port,
        ECOBIN_UART_STATE_SNAPSHOT_PORT_PAYLOAD_MAX_LENGTH);
    transport_send_frame(
        ECOBIN_UART_MESSAGE_STATE_SNAPSHOT_END,
        ECOBIN_UART_FLAG_ACK_REQUIRED,
        g_snapshot_end,
        ECOBIN_UART_STATE_SNAPSHOT_END_PAYLOAD_MAX_LENGTH);
}

static int handle_query_state(const ecobin_uart_frame_view_t *view)
{
    if (view->message_type != ECOBIN_UART_MESSAGE_QUERY_STATE) return 0;
    if (view->payload_length != ECOBIN_UART_QUERY_STATE_PAYLOAD_MAX_LENGTH
        || bytes_are_zero(
            view->payload + ECOBIN_UART_QUERY_STATE_SNAPSHOT_UID_OFFSET,
            16u)
        || !command_digest_is_valid(
            view->message_type,
            view->payload,
            view->payload_length)) {
        send_nack_frame(
            view->tx_sequence,
            view->message_type,
            ECOBIN_UART_NACK_ERROR_INVALID_FIELD);
        return 1;
    }

    if (g_snapshot_valid != 0u
        && memcmp(g_last_query_uid, view->payload, 16u) == 0
        && memcmp(g_last_query_digest, view->payload + 16u, 32u) == 0
        && memcmp(
            g_last_snapshot_uid,
            view->payload + ECOBIN_UART_QUERY_STATE_SNAPSHOT_UID_OFFSET,
            16u) == 0) {
        send_ack_frame(
            view->tx_sequence,
            view->message_type,
            ECOBIN_UART_ACK_DISPOSITION_DUPLICATE_ACCEPTED);
        send_stored_state_snapshot();
        return 1;
    }

    memcpy(g_last_query_uid, view->payload, 16u);
    memcpy(g_last_query_digest, view->payload + 16u, 32u);
    memcpy(
        g_last_snapshot_uid,
        view->payload + ECOBIN_UART_QUERY_STATE_SNAPSHOT_UID_OFFSET,
        16u);
    memcpy(g_latest_mcu_command_uid, view->payload, 16u);
    build_state_snapshot(g_last_query_uid, g_last_snapshot_uid);
    send_ack_frame(
        view->tx_sequence,
        view->message_type,
        ECOBIN_UART_ACK_DISPOSITION_ACCEPTED);
    send_stored_state_snapshot();
    return 1;
}

/* ---- Public API ---- */

static void normalize_boot_id_for_wire(uint8_t boot_id[8])
{
    uint8_t i;
    uint8_t any_nonzero;

    /*
     * UART 1.0 freezes senderBootId to 1..2^53-1 so the value remains exact
     * across the OneNet/JSON path. The current HIL firmware derives this value
     * from the hardware UID; production still requires a persistent monotonic
     * boot counter so that every real reboot receives a different ID.
     */
    boot_id[0] = 0u;
    boot_id[1] &= 0x1Fu;

    any_nonzero = 0u;
    for (i = 0u; i < 8u; ++i) {
        if (boot_id[i] != 0u) {
            any_nonzero = 1u;
            break;
        }
    }
    if (any_nonzero == 0u) {
        boot_id[7] = 1u;
    }
}

void ecobin_transport_init(
    const char *firmware_identity,
    const char *firmware_version)
{
#if !defined(ECOBIN_TRANSPORT_HOST_TEST)
    uint32_t *uid;
#endif
    uint8_t   i;
#if !defined(ECOBIN_TRANSPORT_HOST_TEST)
    uid = (uint32_t *)0x1FFFF7E8u;
#endif
    memset(&g_transport, 0, sizeof(g_transport));
    for (i = 0; i < 8; i++) {
#if defined(ECOBIN_TRANSPORT_HOST_TEST)
        g_transport.boot_id[i] = (uint8_t)(i + 1u);
#else
        g_transport.boot_id[i] = ((uint8_t *)uid)[i];
#endif
    }
    normalize_boot_id_for_wire(g_transport.boot_id);
    g_transport.tx_sequence       = 1u;
    g_transport.event_sequence    = 0u;
    g_transport.firmware_identity = firmware_identity;
    g_transport.firmware_version  = firmware_version;
    ecobin_uart_stream_parser_init(
        &g_transport.parser,
        ECOBIN_UART_SENDER_ROLE_EDGE);
    g_transport.state             = ECOBIN_TRANSPORT_HELLO_SENT;
    g_transport.hello_sent_uptime = g_transport_uptime_ms;
    transport_send_hello();
}

void ecobin_transport_feed_byte(uint8_t byte)
{
    uint64_t now;
    now = (uint64_t)g_transport_uptime_ms;
    ecobin_uart_stream_parser_feed(&g_transport.parser, &byte, 1u,
        now, frame_callback, NULL);
}

void ecobin_transport_tick_100ms(void)
{
    g_transport_uptime_ms += 100u;
}

int ecobin_transport_poll(ecobin_transport_rx_msg_t *rx_msg)
{
    uint8_t idx;
    ecobin_uart_frame_view_t view;

    /*
     * ACK only after the application returned from handling the previously
     * delivered command. This still needs persistent command acceptance for
     * production, but it no longer ACKs before the main loop sees the command.
     */
    if (g_transport.pending_ack != 0u) {
        send_ack_frame(g_transport.pending_ack_tx_seq,
                       g_transport.pending_ack_msg_type,
                       ECOBIN_UART_ACK_DISPOSITION_ACCEPTED);
        g_transport.pending_ack = 0u;
    }

    /* Keep retrying HELLO until both directions have been accepted. */
    if (g_transport.state != ECOBIN_TRANSPORT_READY) {
        uint32_t now;
        uint32_t elapsed;
        now = g_transport_uptime_ms;
        elapsed = now - g_transport.hello_sent_uptime;
        if (elapsed >= HELLO_RETRY_MS) {
            transport_send_hello();
            g_transport.hello_sent_uptime = now;
        }
    }

    while (g_transport.rx_head != g_transport.rx_tail) {
        idx = g_transport.rx_head;
        view = g_transport.rx_queue[idx].view;
        if (view.payload_length > 0u) {
            memcpy(
                g_transport.delivered_payload,
                g_transport.rx_queue[idx].data + 12u,
                view.payload_length);
        }
        view.payload = g_transport.delivered_payload;
        g_transport.rx_head = (uint8_t)((idx + 1u) % RX_QUEUE_SIZE);

        if (view.message_type == ECOBIN_UART_MESSAGE_HELLO) {
            handle_edge_hello(&view);
            continue;
        }
        if (view.message_type == ECOBIN_UART_MESSAGE_HELLO_ACK) {
            handle_edge_hello_ack(&view);
            continue;
        }
        if (view.message_type == ECOBIN_UART_MESSAGE_ACK) {
            continue;
        }
        if (view.message_type == ECOBIN_UART_MESSAGE_NACK) {
            g_transport.diagnostics |= ECOBIN_UART_DIAG_SEMANTIC_REJECTED;
            continue;
        }
        if (g_transport.state != ECOBIN_TRANSPORT_READY) {
            continue;
        }
        if (handle_query_state(&view) || handle_config_command(&view)) {
            continue;
        }

        rx_msg->type           = view.message_type;
        rx_msg->payload        = g_transport.delivered_payload;
        rx_msg->payload_length = view.payload_length;
        rx_msg->tx_sequence    = view.tx_sequence;
        if (ecobin_uart_message_ack_required(view.message_type)) {
            g_transport.pending_ack          = 1u;
            g_transport.pending_ack_tx_seq   = view.tx_sequence;
            g_transport.pending_ack_msg_type = view.message_type;
        }
        return 1;
    }
    return 0;
}

ecobin_transport_state_t ecobin_transport_get_state(void)
{
    return g_transport.state;
}

/* ---- Send functions ---- */

void ecobin_transport_send_door_state(
    ecobin_uart_delivery_door_state_t  state,
    ecobin_uart_delivery_door_health_t health)
{
    uint8_t payload[ECOBIN_UART_DELIVERY_DOOR_STATE_CHANGED_PAYLOAD_MAX_LENGTH];
    uint32_t uptime_copy;
    memset(payload, 0, sizeof(payload));
    g_transport.event_sequence++;
    uptime_copy = g_transport_uptime_ms;
    memcpy(payload + ECOBIN_UART_DELIVERY_DOOR_STATE_CHANGED_MCU_BOOT_ID_OFFSET,
           g_transport.boot_id, 8);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_DELIVERY_DOOR_STATE_CHANGED_MCU_EVENT_SEQUENCE_OFFSET,
        g_transport.event_sequence);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_DELIVERY_DOOR_STATE_CHANGED_UPTIME_MS_OFFSET,
        uptime_copy);
    payload[ECOBIN_UART_DELIVERY_DOOR_STATE_CHANGED_PORT_NO_OFFSET]   = 1u;
    payload[ECOBIN_UART_DELIVERY_DOOR_STATE_CHANGED_DOOR_STATE_OFFSET]  = state;
    payload[ECOBIN_UART_DELIVERY_DOOR_STATE_CHANGED_DOOR_HEALTH_OFFSET] = health;
    transport_send_frame(ECOBIN_UART_MESSAGE_DELIVERY_DOOR_STATE_CHANGED,
        ECOBIN_UART_FLAG_ACK_REQUIRED, payload,
        ECOBIN_UART_DELIVERY_DOOR_STATE_CHANGED_PAYLOAD_MAX_LENGTH);
}

void ecobin_transport_send_safety_sensor_event(
    ecobin_uart_smoke_state_t   smoke_state,
    ecobin_uart_sensor_health_t sensor_health,
    uint16_t                    fault_code)
{
    uint8_t payload[ECOBIN_UART_SAFETY_SENSOR_EVENT_PAYLOAD_MAX_LENGTH];
    uint32_t uptime_copy;
    memset(payload, 0, sizeof(payload));
    g_transport.event_sequence++;
    uptime_copy = g_transport_uptime_ms;
    memcpy(payload + ECOBIN_UART_SAFETY_SENSOR_EVENT_MCU_BOOT_ID_OFFSET,
           g_transport.boot_id, 8);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_SAFETY_SENSOR_EVENT_MCU_EVENT_SEQUENCE_OFFSET,
        g_transport.event_sequence);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_SAFETY_SENSOR_EVENT_UPTIME_MS_OFFSET,
        uptime_copy);
    payload[ECOBIN_UART_SAFETY_SENSOR_EVENT_SMOKE_STATE_OFFSET]         = smoke_state;
    payload[ECOBIN_UART_SAFETY_SENSOR_EVENT_SMOKE_SENSOR_HEALTH_OFFSET] = sensor_health;
    ecobin_uart_write_u16_be(
        payload + ECOBIN_UART_SAFETY_SENSOR_EVENT_FAULT_CODE_OFFSET, fault_code);
    payload[ECOBIN_UART_SAFETY_SENSOR_EVENT_PORT_NO_OFFSET] = 1u;
    transport_send_frame(ECOBIN_UART_MESSAGE_SAFETY_SENSOR_EVENT,
        ECOBIN_UART_FLAG_ACK_REQUIRED, payload,
        ECOBIN_UART_SAFETY_SENSOR_EVENT_PAYLOAD_MAX_LENGTH);
}

void ecobin_transport_send_postclose_weight(
    int32_t                          stable_weight_grams,
    int32_t                          last_observed_weight_grams,
    ecobin_uart_measurement_status_t status,
    ecobin_uart_sensor_health_t      sensor_health,
    uint16_t                         fault_code)
{
    uint8_t payload[ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_PAYLOAD_MAX_LENGTH];
    uint32_t uptime_copy;
    memset(payload, 0, sizeof(payload));
    g_transport.event_sequence++;
    uptime_copy = g_transport_uptime_ms;
    memcpy(payload + ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_MCU_BOOT_ID_OFFSET,
           g_transport.boot_id, 8);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_MCU_EVENT_SEQUENCE_OFFSET,
        g_transport.event_sequence);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_UPTIME_MS_OFFSET,
        uptime_copy);
    payload[ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_PORT_NO_OFFSET] = 1u;
    payload[ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_MEASUREMENT_STATUS_OFFSET] = status;
    payload[ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_STABLE_WEIGHT_VALID_OFFSET] = 1u;
    ecobin_uart_write_i32_be(
        payload + ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_STABLE_WEIGHT_GRAMS_OFFSET,
        stable_weight_grams);
    payload[ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_LAST_OBSERVED_WEIGHT_VALID_OFFSET] = 1u;
    ecobin_uart_write_i32_be(
        payload + ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_LAST_OBSERVED_WEIGHT_GRAMS_OFFSET,
        last_observed_weight_grams);
    payload[ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_WEIGHT_SENSOR_HEALTH_OFFSET] = sensor_health;
    ecobin_uart_write_u16_be(
        payload + ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_FAULT_CODE_OFFSET, fault_code);
    transport_send_frame(ECOBIN_UART_MESSAGE_WORK_POSTCLOSE_WEIGHT_READY,
        ECOBIN_UART_FLAG_ACK_REQUIRED, payload,
        ECOBIN_UART_WORK_POSTCLOSE_WEIGHT_READY_PAYLOAD_MAX_LENGTH);
}

void ecobin_transport_send_fullness_result(
    ecobin_uart_infrared_value_t      infrared_value,
    ecobin_uart_sensor_health_t       infrared_health,
    int32_t                           stable_weight_grams,
    ecobin_uart_measurement_status_t  measurement_status,
    ecobin_uart_sensor_health_t       weight_sensor_health,
    uint16_t                          fault_code)
{
    uint8_t payload[ECOBIN_UART_FULLNESS_SAMPLE_RESULT_PAYLOAD_MAX_LENGTH];
    uint32_t uptime_copy;
    memset(payload, 0, sizeof(payload));
    g_transport.event_sequence++;
    uptime_copy = g_transport_uptime_ms;
    memcpy(payload + ECOBIN_UART_FULLNESS_SAMPLE_RESULT_MCU_BOOT_ID_OFFSET,
           g_transport.boot_id, 8);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_FULLNESS_SAMPLE_RESULT_MCU_EVENT_SEQUENCE_OFFSET,
        g_transport.event_sequence);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_FULLNESS_SAMPLE_RESULT_UPTIME_MS_OFFSET,
        uptime_copy);
    payload[ECOBIN_UART_FULLNESS_SAMPLE_RESULT_PORT_NO_OFFSET]          = 1u;
    payload[ECOBIN_UART_FULLNESS_SAMPLE_RESULT_SAMPLE_ROLE_OFFSET]     = ECOBIN_UART_SAMPLE_ROLE_INITIAL;
    payload[ECOBIN_UART_FULLNESS_SAMPLE_RESULT_INFRARED_VALUE_OFFSET]   = infrared_value;
    payload[ECOBIN_UART_FULLNESS_SAMPLE_RESULT_INFRARED_HEALTH_OFFSET]  = infrared_health;
    payload[ECOBIN_UART_FULLNESS_SAMPLE_RESULT_MEASUREMENT_STATUS_OFFSET] = measurement_status;
    payload[ECOBIN_UART_FULLNESS_SAMPLE_RESULT_STABLE_WEIGHT_VALID_OFFSET] = 1u;
    ecobin_uart_write_i32_be(
        payload + ECOBIN_UART_FULLNESS_SAMPLE_RESULT_STABLE_WEIGHT_GRAMS_OFFSET,
        stable_weight_grams);
    payload[ECOBIN_UART_FULLNESS_SAMPLE_RESULT_WEIGHT_SENSOR_HEALTH_OFFSET] = weight_sensor_health;
    ecobin_uart_write_u16_be(
        payload + ECOBIN_UART_FULLNESS_SAMPLE_RESULT_FAULT_CODE_OFFSET, fault_code);
    transport_send_frame(ECOBIN_UART_MESSAGE_FULLNESS_SAMPLE_RESULT,
        ECOBIN_UART_FLAG_ACK_REQUIRED, payload,
        ECOBIN_UART_FULLNESS_SAMPLE_RESULT_PAYLOAD_MAX_LENGTH);
}

void ecobin_transport_send_fault(
    ecobin_uart_fault_component_t component,
    ecobin_uart_fault_severity_t  severity,
    ecobin_uart_fault_lifecycle_t lifecycle,
    uint16_t                      fault_code)
{
    uint8_t payload[ECOBIN_UART_FAULT_OBSERVED_PAYLOAD_MAX_LENGTH];
    uint32_t uptime_copy;
    memset(payload, 0, sizeof(payload));
    g_transport.event_sequence++;
    uptime_copy = g_transport_uptime_ms;
    memcpy(payload + ECOBIN_UART_FAULT_OBSERVED_MCU_BOOT_ID_OFFSET,
           g_transport.boot_id, 8);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_FAULT_OBSERVED_MCU_EVENT_SEQUENCE_OFFSET,
        g_transport.event_sequence);
    ecobin_uart_write_u32_be(
        payload + ECOBIN_UART_FAULT_OBSERVED_UPTIME_MS_OFFSET,
        uptime_copy);
    payload[ECOBIN_UART_FAULT_OBSERVED_LIFECYCLE_OFFSET]  = lifecycle;
    payload[ECOBIN_UART_FAULT_OBSERVED_COMPONENT_OFFSET]  = component;
    payload[ECOBIN_UART_FAULT_OBSERVED_SEVERITY_OFFSET]   = severity;
    ecobin_uart_write_u16_be(
        payload + ECOBIN_UART_FAULT_OBSERVED_FAULT_CODE_OFFSET, fault_code);
    payload[ECOBIN_UART_FAULT_OBSERVED_PORT_NO_OFFSET] = 1u;
    transport_send_frame(ECOBIN_UART_MESSAGE_FAULT_OBSERVED,
        ECOBIN_UART_FLAG_ACK_REQUIRED, payload,
        ECOBIN_UART_FAULT_OBSERVED_PAYLOAD_MAX_LENGTH);
}
