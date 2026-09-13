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
#include "runtime_clock.h"
#include "scale_reader.h"
#include "firmware_identity.h"
#define ECOBIN_MCU_RUNTIME_INCLUDE_WEIGHT_POLL
#include "mcu_runtime_logic.h"

NativeRxBuffer NativeControlRx;
NativeScaleTransport NativeScaleRx;
void NativeUsart_InitBuffers(void) {
    NativeRx_Init(&NativeControlRx);
    NativeScale_Init(&NativeScaleRx);
}
static uint8_t NativeUsart_Wait(USART_TypeDef *uart, uint16_t flag) {
    uint32_t remaining = 100000u;
    while (USART_GetFlagStatus(uart, flag) == RESET) if (--remaining == 0u) return 0u;
    return 1u;
}
uint8_t NativeUsart_SendControl(const uint8_t *bytes, size_t length) {
    size_t i;
    if (bytes == 0 || length > 256u) return 0u;
    for (i = 0u; i < length; ++i) {
        if (!NativeUsart_Wait(USART1, USART_FLAG_TXE)) return 0u;
        USART_SendData(USART1, bytes[i]);
    }
    return NativeUsart_Wait(USART1, USART_FLAG_TC);
}
uint8_t NativeUsart_SendScaleQuery(void) {
    static const uint8_t request[8] = {1u, 3u, 0u, 0u, 0u, 2u, 0xc4u, 0x0bu};
    uint8_t i, ok = 1u;
    GPIO_SetBits(GPIOA, GPIO_Pin_1);
    for (i = 0u; i < 8u; ++i) {
        if (!NativeUsart_Wait(USART2, USART_FLAG_TXE)) { ok = 0u; break; }
        USART_SendData(USART2, request[i]);
    }
    if (!NativeUsart_Wait(USART2, USART_FLAG_TC)) ok = 0u;
    GPIO_ResetBits(GPIOA, GPIO_Pin_1);
    return ok;
}
void NativeUsart_ReceiveScaleIrq(uint8_t byte) {
    uint32_t previous = __get_PRIMASK();
    __disable_irq();
    NativeScale_ReceiveIrq(&NativeScaleRx, byte, RuntimeClock_Now64Locked());
    __set_PRIMASK(previous);
}

/* RS485�������: RE/DE����PA1 */
#define Set_RE  GPIO_SetBits(GPIOA,GPIO_Pin_1);
#define Clr_RE  GPIO_ResetBits(GPIOA,GPIO_Pin_1);
#define Set_DE  GPIO_SetBits(GPIOA,GPIO_Pin_1);
#define Clr_DE  GPIO_ResetBits(GPIOA,GPIO_Pin_1);

/* RS485 ���ջ����� */
unsigned char RS485_RxBuf[RS485_RX_BUF_SIZE];
volatile unsigned char RS485_RxLen = 0;

/* printf �ض��� USART1, ���Դ�ӡ�� */
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
    return ScaleReader_Crc16(buf, len);
}

/* Keep the legacy unsigned DD/EF/F1 projection until native result metadata is
 * available. The new decoder itself preserves signed grams without clamping. */
static unsigned char DecodeLegacyScaleResponse(unsigned long *weight)
{
    uint8_t frame[9];
    uint8_t length, i, status;
    int32_t grams = 0;
    uint32_t previous = __get_PRIMASK();
    __disable_irq();
    length = RS485_RxLen;
    for(i = 0U; i < 9U; i++) frame[i] = RS485_RxBuf[i];
    __set_PRIMASK(previous);
    status = ScaleReader_Decode(frame, length, INT32_MIN, MCU_WEIGHT_MAX_GRAMS, &grams);
    if(status == SCALE_READER_OK)
        *weight = grams < 0 ? 0UL : (unsigned long)grams;
    return status;
}

/*
 * ��ȡ����ģ������
 * ����: 01 03 00 00 00 02 C4 0B
 * ��Ӧ: 01 03 04 Data_H Data_L 00 00 CRC_L CRC_H
 * ����: weight - ��������ֵ
 * ����: 0=�ɹ�, 1=��ʱ, 2=����̫��, 3=CRC����
 */
unsigned char Weight_Read(unsigned long *weight)
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
	while(RS485_RxLen < 9)
	{
		DelayNuS(1000);
		timeout++;
		if(timeout > 50000)
			return 1;  //��ʱ
	}

	//�յ�����1�ֽں�,�ȴ�ʣ������(Modbus֡���3.5�ַ���3.6ms@9600)
	DelayNuS(4000);  //�ȴ�֡�������
	if(RS485_RxLen < 9)
		return 2;  //����̫��

    return DecodeLegacyScaleResponse(weight);
}

/* ===== 非阻塞称重状态机 (配合TIM3定时器) ===== */
WeightState weight_state = WEIGHT_IDLE;
static uint32_t weight_start_ms;

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
    weight_start_ms = RuntimeClock_Now();
    weight_state = WEIGHT_SENT;
}

/*
 * 轮询重量读取结果 (非阻塞)
 * 参数: weight - 输出重量值
 * 返回: 0=成功, 1=超时, 2=等待中(数据未到齐), 3=CRC错误
 * 成功/超时/CRC错误后状态自动复位为 WEIGHT_IDLE
 */
unsigned char Weight_Read_Poll(unsigned long *weight)
{
    uint32_t elapsed_ms;
    unsigned char decode_status;
    unsigned char poll_status;

    if(weight_state != WEIGHT_SENT)
        return 2;  /* 未在等待状态 */

    /*
     * Prefer a complete ISR-captured response over the elapsed deadline.
     * F0 ultrasonic ranging can block the main loop beyond 200ms even though
     * all Modbus bytes have already arrived in RS485_RxBuf.
     */
    elapsed_ms = (uint32_t)(RuntimeClock_Now() - weight_start_ms);
    poll_status = McuRuntime_WeightPollDecision(RS485_RxLen, elapsed_ms);
    if(poll_status == MCU_WEIGHT_POLL_TIMEOUT)
    {
        weight_state = WEIGHT_IDLE;
        return 1;  /* 超时 */
    }
    if(poll_status == MCU_WEIGHT_POLL_WAITING)
        return 2;  /* 等待中 */

    decode_status = DecodeLegacyScaleResponse(weight);
    weight_state = WEIGHT_IDLE;
    return decode_status;
}

/* ��ʼ��USART2 */
void USART2_int(void)
{
  USART2_Config();
}

/* ===== USART1 视觉模块通信 ===== */
unsigned char Vision_RxBuf[VISION_RX_BUF_SIZE];
volatile unsigned char Vision_RxLen = 0;

/* URL buffer (A0 frame) */
unsigned char url_buffer[URL_BUF_SIZE];
unsigned char url_len = 0;

/* Get frame length by header byte (protocol v2.0) */
unsigned char GetRxFrameLen(unsigned char header)
{
    switch(header)
    {
        case 0xAA: return 3;
        case 0xBB: return 3;
        case 0xEE: return 3;
        case 0xF0: return 3;
        case 0xF2: return 3;
        case 0xA0: return 195;
        default:   return 0;
    }
}

static void Vision_SendByte(unsigned char value)
{
    USART_SendData(USART1, value);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
}


/* BB + status + BB: 溢满标志 */
void Vision_SendOverflow(unsigned char status)
{
    USART_SendData(USART1, 0xBB);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, status);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, 0xBB);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
}

/* CC + status + CC: 烟雾报警 */
void Vision_SendSmoke(unsigned char status)
{
    USART_SendData(USART1, 0xCC);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, status);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, 0xCC);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
}

/* DD + weight(3字节, 高位在前) + DD: 垃圾结果重量 */
void Vision_SendWeight(unsigned long weight)
{
    USART_SendData(USART1, 0xDD);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(weight >> 16));       /* 最高字节 */
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(weight >> 8));        /* 中间字节 */
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(weight & 0xFF));      /* 最低字节 */
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, 0xDD);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
}


/* Delivery result report (protocol v2.0, 9-byte frame) */
void Vision_SendDeliveryResult(unsigned long pre_weight, unsigned long post_weight, unsigned char full)
{
    USART_SendData(USART1, 0xDD);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(pre_weight >> 16));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(pre_weight >> 8));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(pre_weight & 0xFF));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(post_weight >> 16));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(post_weight >> 8));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(post_weight & 0xFF));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, full);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, 0xDD);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
}

/* Cleaning result report (protocol v2.0, 9-byte frame) */
void Vision_SendCleaningResult(unsigned long pre_weight, unsigned long post_weight, unsigned char full)
{
    USART_SendData(USART1, 0xEF);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(pre_weight >> 16));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(pre_weight >> 8));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(pre_weight & 0xFF));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(post_weight >> 16));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(post_weight >> 8));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(post_weight & 0xFF));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, full);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, 0xEF);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
}

/* Sensor self-test response (protocol v2.0, 8-byte frame) */
void Vision_SendSelfTestResult(unsigned char valid, unsigned long weight,
                               unsigned char dist, unsigned char smoke)
{
    USART_SendData(USART1, 0xF1);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, valid);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(weight >> 16));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(weight >> 8));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, (unsigned char)(weight & 0xFF));
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, dist);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, smoke);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    USART_SendData(USART1, 0xF1);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
}

/*
 * F3 firmware/status snapshot, fixed-frame revision 2.
 *
 * F3 MODE STATUS PROTOCOL_REV VERSION_CODE_BE[4] VERSION_LEN
 * VERSION_ASCII[32] FIRMWARE_IDENTITY[8] SAFE_FLAGS F3
 *
 * This is deliberately not a generic ACK and does not add CRC to the
 * negotiated fixed-frame protocol.
 */
void Vision_SendFirmwareStatus(unsigned char mode, unsigned char status,
                               unsigned char safe_flags)
{
    static const unsigned char version[] = ECOBIN_MCU_FIRMWARE_VERSION;
    static const unsigned char identity[8] =
        ECOBIN_MCU_FIRMWARE_IDENTITY_BYTES;
    unsigned char version_len = (unsigned char)(sizeof(version) - 1);
    unsigned char i;

    if(version_len > 32) version_len = 32;

    Vision_SendByte(0xF3);
    Vision_SendByte(mode);
    Vision_SendByte(status);
    Vision_SendByte(0x02); /* fixed-frame revision */
    Vision_SendByte((unsigned char)(ECOBIN_MCU_FIRMWARE_VERSION_CODE >> 24));
    Vision_SendByte((unsigned char)(ECOBIN_MCU_FIRMWARE_VERSION_CODE >> 16));
    Vision_SendByte((unsigned char)(ECOBIN_MCU_FIRMWARE_VERSION_CODE >> 8));
    Vision_SendByte((unsigned char)ECOBIN_MCU_FIRMWARE_VERSION_CODE);
    Vision_SendByte(version_len);
    for(i = 0; i < 32; i++)
        Vision_SendByte(i < version_len ? version[i] : 0x00);
    for(i = 0; i < 8; i++)
        Vision_SendByte(identity[i]);
    Vision_SendByte(safe_flags);
    Vision_SendByte(0xF3);
}
