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

#include "usart1.h"

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

/* =====================================================================
 * ecobin UART 协议传输层 (USART1 ↔ 橙派/Edge)
 * 基于 ecobin_uart_protocol.h，替换原有的 AA/BB/CC/DD 简单帧
 * ===================================================================== */

#define RX_QUEUE_SIZE         4
#define TX_BUF_SIZE           ECOBIN_UART_MAX_FRAME_LENGTH
#define HELLO_RETRY_MS        1000
#define HELLO_MAX_RETRIES     5

volatile uint32_t g_transport_uptime_ms = 0;

static struct {
    ecobin_uart_stream_parser_t  parser;
    struct {
        uint8_t                  data[ECOBIN_UART_MAX_FRAME_LENGTH];
        uint16_t                 length;
        ecobin_uart_frame_view_t view;
    } rx_queue[RX_QUEUE_SIZE];
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
    uint8_t  hello_retry_count;
    const char *firmware_identity;
    volatile uint32_t diagnostics;
} g_transport;

/* Blocking send raw bytes via USART1 */
static void transport_usart1_send(const uint8_t *data, uint16_t len)
{
    uint16_t i;
    for (i = 0; i < len; i++) {
        USART_SendData(USART1, data[i]);
        while (USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    }
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
static void send_ack_frame(uint32_t ref_tx_seq, uint8_t ref_msg_type)
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
    payload[ECOBIN_UART_ACK_DISPOSITION_OFFSET] = ECOBIN_UART_ACK_DISPOSITION_ACCEPTED;
    transport_send_frame(ECOBIN_UART_MESSAGE_ACK, 0, payload,
                         ECOBIN_UART_ACK_PAYLOAD_MAX_LENGTH);
}

static int validate_rx_length(uint8_t msg_type, uint16_t len)
{
    switch (msg_type) {
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
    (void)context;

    if (!validate_rx_length(view->message_type, view->payload_length)) {
        g_transport.diagnostics |= ECOBIN_UART_DIAG_SEMANTIC_REJECTED;
        return;
    }

    switch (view->message_type) {
    case ECOBIN_UART_MESSAGE_HELLO_ACK:
        if (view->payload_length >= ECOBIN_UART_HELLO_ACK_PAYLOAD_MIN_LENGTH) {
            memcpy(g_transport.edge_boot_id,
                   view->payload + ECOBIN_UART_HELLO_ACK_RESPONDER_BOOT_ID_OFFSET, 8);
            if (g_transport.state == ECOBIN_TRANSPORT_HELLO_SENT)
                g_transport.state = ECOBIN_TRANSPORT_READY;
        }
        break;
    case ECOBIN_UART_MESSAGE_ACK:
        if (g_transport.pending_ack != 0u
            && g_transport.pending_ack_tx_seq == view->tx_sequence)
            g_transport.pending_ack = 0u;
        break;
    case ECOBIN_UART_MESSAGE_NACK:
        g_transport.diagnostics |= ECOBIN_UART_DIAG_SEMANTIC_REJECTED;
        break;
    default:
        next = (uint8_t)((g_transport.rx_tail + 1u) % RX_QUEUE_SIZE);
        if (next == g_transport.rx_head) { g_transport.rx_overflow = 1u; return; }
        memcpy(g_transport.rx_queue[g_transport.rx_tail].data, frame, length);
        g_transport.rx_queue[g_transport.rx_tail].length = (uint16_t)length;
        g_transport.rx_queue[g_transport.rx_tail].view  = *view;
        g_transport.rx_tail = next;
        if (ecobin_uart_message_ack_required(view->message_type)) {
            g_transport.pending_ack          = 1u;
            g_transport.pending_ack_tx_seq   = view->tx_sequence;
            g_transport.pending_ack_msg_type = view->message_type;
        }
        break;
    }
}

/* Build and send HELLO frame */
static void transport_send_hello(void)
{
    uint8_t  payload[91];
    uint8_t  fw_len;
    uint64_t caps;
    uint16_t total_len;
    memset(payload, 0, sizeof(payload));
    payload[ECOBIN_UART_HELLO_SENDER_ROLE_OFFSET] = ECOBIN_UART_SENDER_MCU;
    memcpy(payload + ECOBIN_UART_HELLO_SENDER_BOOT_ID_OFFSET, g_transport.boot_id, 8);
    payload[ECOBIN_UART_HELLO_SUPPORTED_MAJOR_OFFSET] = ECOBIN_UART_PROTOCOL_MAJOR;
    payload[ECOBIN_UART_HELLO_MINIMUM_MINOR_OFFSET]   = ECOBIN_UART_PROTOCOL_MINOR;
    payload[ECOBIN_UART_HELLO_MAXIMUM_MINOR_OFFSET]   = ECOBIN_UART_PROTOCOL_MINOR;
    payload[ECOBIN_UART_HELLO_PORT_COUNT_OFFSET]      = 1u;
    caps = ECOBIN_UART_CAPABILITY_DELIVERY_DOOR_CONTROL
         | ECOBIN_UART_CAPABILITY_DELIVERY_DOOR_POSITION_FEEDBACK
         | ECOBIN_UART_CAPABILITY_CLEAN_SOLENOID_CONTROL
         | ECOBIN_UART_CAPABILITY_STABLE_SIGNED_WEIGHT
         | ECOBIN_UART_CAPABILITY_INFRARED_SAMPLING
         | ECOBIN_UART_CAPABILITY_SMOKE_MONITORING
         | ECOBIN_UART_CAPABILITY_PERSISTENT_CRITICAL_EVENTS;
    ecobin_uart_write_u64_be(payload + ECOBIN_UART_HELLO_CAPABILITY_BITMAP_OFFSET, caps);
    ecobin_uart_write_u16_be(payload + ECOBIN_UART_HELLO_MAXIMUM_FRAME_LENGTH_OFFSET,
        ECOBIN_UART_MAX_FRAME_LENGTH);
    ecobin_uart_write_u16_be(payload + ECOBIN_UART_HELLO_PENDING_CRITICAL_EVENT_COUNT_OFFSET, 0u);
    fw_len = (uint8_t)strlen(g_transport.firmware_identity);
    if (fw_len > 66u) fw_len = 66u;
    payload[ECOBIN_UART_HELLO_FIRMWARE_IDENTITY_OFFSET] = fw_len;
    memcpy(payload + ECOBIN_UART_HELLO_FIRMWARE_IDENTITY_OFFSET + 1u,
           g_transport.firmware_identity, fw_len);
    total_len = (uint16_t)(ECOBIN_UART_HELLO_FIRMWARE_IDENTITY_OFFSET + 1u + fw_len);
    transport_send_frame(ECOBIN_UART_MESSAGE_HELLO, 0, payload, total_len);
}

/* ---- Public API ---- */

void ecobin_transport_init(const char *firmware_identity)
{
    uint32_t *uid;
    uint8_t   i;
    uid = (uint32_t *)0x1FFFF7E8u;
    memset(&g_transport, 0, sizeof(g_transport));
    for (i = 0; i < 8; i++) g_transport.boot_id[i] = ((uint8_t *)uid)[i];
    g_transport.tx_sequence       = 1u;
    g_transport.event_sequence    = 0u;
    g_transport.firmware_identity = firmware_identity;
    ecobin_uart_stream_parser_init(&g_transport.parser, ECOBIN_UART_SENDER_MCU);
    g_transport.state             = ECOBIN_TRANSPORT_HELLO_SENT;
    g_transport.hello_sent_uptime = g_transport_uptime_ms;
    g_transport.hello_retry_count = 0u;
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

    /* Handle deferred ACK */
    if (g_transport.pending_ack != 0u) {
        send_ack_frame(g_transport.pending_ack_tx_seq,
                       g_transport.pending_ack_msg_type);
        g_transport.pending_ack = 0u;
    }

    /* HELLO retry */
    if (g_transport.state == ECOBIN_TRANSPORT_HELLO_SENT) {
        uint32_t now;
        uint32_t elapsed;
        now = g_transport_uptime_ms;
        elapsed = now - g_transport.hello_sent_uptime;
        if (elapsed >= HELLO_RETRY_MS
            && g_transport.hello_retry_count < HELLO_MAX_RETRIES) {
            transport_send_hello();
            g_transport.hello_sent_uptime = now;
            g_transport.hello_retry_count++;
        }
    }

    /* Dequeue */
    if (g_transport.rx_head == g_transport.rx_tail) return 0;
    idx = g_transport.rx_head;
    g_transport.rx_head = (uint8_t)((idx + 1u) % RX_QUEUE_SIZE);
    rx_msg->type           = g_transport.rx_queue[idx].view.message_type;
    rx_msg->payload        = g_transport.rx_queue[idx].view.payload;
    rx_msg->payload_length = g_transport.rx_queue[idx].view.payload_length;
    rx_msg->tx_sequence    = g_transport.rx_queue[idx].view.tx_sequence;
    return 1;
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
