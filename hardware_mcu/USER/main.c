/******************** (C) COPYRIGHT 2019 Designed by Captain *********************
 * 文件名  ：main.c
 * 功能    ：RS485称重传感器读取 + 继电器控制(智能垃圾桶)
 * 实现平台：STM32F103C8T6工控板
 * 版本    ：ST3.5.0
 * 店主    ：踏上电子工作室
 * 淘宝店  ：https://shop151358311.taobao.com/
*********************************************************************************/

#include "stm32f10x.h"
#include "usart1.h"
#include "usart3.h"
#include "led.h"
#include "sys.h"
#include "adc.h"
#include "smoke_monitor.h"
#include "mcu_update_execution.h"

/* ===== 定时器驱动重量采集: 全局变量 (100ms周期) ===== */
volatile unsigned char  g_weight_tick = 0;
volatile unsigned short g_tick_count  = 0;

#define SUO   PBout(8)

#define RELAY1   PBout(6)//控制推杆方向
#define RELAY2   PBout(7)

#define LIMIT_SW1   PBin(4)   /* 下: 开盖到位 */
#define LIMIT_SW2   PBin(5)   /* 上: 关盖到位 */

/* HC-SR04 超声波溢满检测 */
#define HCSR04_TRIG      PAout(11)   /* PA11=TRIG 触发输出 */
#define HCSR04_ECHO      PAin(12)    /* PA12=ECHO 回波输入 */
#define OVERFLOW_DIST    30          /* 距离<30cm判定溢满 */

/* 单价 (元/kg), 视觉模块可修改 */
unsigned char unit_price = 8;

/* 烟雾阈值: MQ-2电压>1.5V 判定有烟雾 (0~3.3V, 对应ADC 0~4095) */
#define SMOKE_THRESHOLD  1800   /* 1.5V / 3.3V * 4095 ≈ 1860 */

/* 推杆方向定义 */
#define DIR_STOP    0   /* 停止 */
#define Close_PB4  1   /* 伸长/关盖: RELAY1=0 RELAY2=1 (+24V) */
#define Open_PB5 2   /* 缩回/开盖: RELAY1=1 RELAY2=0 (-24V) */

/* 注: PA11/PA12 已分配给 HC-SR04, 见上方 HCSR04_TRIG/HCSR04_ECHO */

/* ===== 称重状态机 ===== */
#define WEIGH_IDLE    0   /* 空闲: 等待03指令 */
#define WEIGH_ACTIVE  1   /* 测重中: 收到03后监测垃圾重量, 发送page6 */
#define WEIGH_RESULT  2   /* 结果: 收到04后显示page7, 停止page6 */

/* 称重状态机变量 */
unsigned long  baseline_total     = 0;   /* 投放前总重 (上电或02时更新) */
unsigned long  stable_garbage     = 0;   /* 当前稳定的垃圾重量值 */
unsigned char  garbage_stable_cnt = 0;   /* 垃圾重量连续稳定计数 */
unsigned char  baseline_inited    = 0;   /* baseline是否已初始化 */

/* Weight moving-average filter (4 samples @600ms = 2.4s window) */
#define WEIGHT_FILTER_N  4
static unsigned long weight_history[WEIGHT_FILTER_N];
static unsigned char weight_hist_cnt = 0;
volatile unsigned char g_weight_updated = 0;

/* Protocol v2.0 state */
unsigned long  delivery_pre_weight = 0;
unsigned long  cleaning_pre_weight = 0;
unsigned char  delivery_flow_active = 0;
#define CLEAN_IDLE          0
#define CLEAN_LOCK_ON       1
#define CLEAN_WAIT_CONFIRM  2
unsigned char  cleaning_state = CLEAN_IDLE;
unsigned char  lock_timer_ticks = 0;
unsigned char  update_prepared = 0;
unsigned char  weigh_state = WEIGH_IDLE;

/* 稳定判定: 连续3次读数波动≤5g视为稳定 */
#define STABLE_THRESHOLD  5
#define STABLE_COUNT      3

/* 全局变量 */
unsigned long  g_weight;                  /* 当前重量读数, 供状态机使用 */
unsigned short kg, bg;                    /* 千克/百克 */
unsigned long  total_price;
unsigned short yuan, jiao, fen;

void Delay(vu32 nCount)
{
    for(; nCount != 0; nCount--);
}
/* 4-point moving average: reduces sensor jitter on display */
unsigned long Weight_Filter(unsigned long raw)
{
    unsigned long sum = 0;
    unsigned char i;
    for(i = 0; i < WEIGHT_FILTER_N - 1; i++)
        weight_history[i] = weight_history[i + 1];
    weight_history[WEIGHT_FILTER_N - 1] = raw;
    if(weight_hist_cnt < WEIGHT_FILTER_N) weight_hist_cnt++;
    for(i = WEIGHT_FILTER_N - weight_hist_cnt; i < WEIGHT_FILTER_N; i++)
        sum += weight_history[i];
    return sum / weight_hist_cnt;
}


/*
 * TIM3 初始化: 100ms 周期中断
 * APB1=36MHz, 因APB1预分频≠1, TIM3时钟=72MHz
 * 预分频=7200 → 10kHz, 自动重载=1000 → 100ms
 */
void TIM3_Init(void)
{
    TIM_TimeBaseInitTypeDef TIM_TimeBaseStructure;
    NVIC_InitTypeDef NVIC_InitStructure;

    RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM3, ENABLE);

    TIM_TimeBaseStructure.TIM_Period        = 1000 - 1;
    TIM_TimeBaseStructure.TIM_Prescaler      = 7200 - 1;
    TIM_TimeBaseStructure.TIM_ClockDivision  = TIM_CKD_DIV1;
    TIM_TimeBaseStructure.TIM_CounterMode    = TIM_CounterMode_Up;
    TIM_TimeBaseInit(TIM3, &TIM_TimeBaseStructure);

    TIM_ITConfig(TIM3, TIM_IT_Update, ENABLE);

    NVIC_InitStructure.NVIC_IRQChannel            = TIM3_IRQn;
    NVIC_InitStructure.NVIC_IRQChannelSubPriority = 2;
    NVIC_InitStructure.NVIC_IRQChannelCmd         = ENABLE;
    NVIC_Init(&NVIC_InitStructure);

    TIM_Cmd(TIM3, ENABLE);
}

/*
 * HC-SR04 超声波测距初始化 (TIM4: 1MHz时基, 1μs精度)
 * APB1=36MHz, TIM4时钟=72MHz, 预分频=72 → 1MHz
 */
void HCSR04_Init(void)
{
    TIM_TimeBaseInitTypeDef TIM_TimeBaseStructure;

    RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM4, ENABLE);

    TIM_TimeBaseStructure.TIM_Period        = 65535;   /* 最大65ms, 覆盖4m量程 */
    TIM_TimeBaseStructure.TIM_Prescaler      = 72 - 1;   /* 72MHz / 72 = 1MHz */
    TIM_TimeBaseStructure.TIM_ClockDivision  = TIM_CKD_DIV1;
    TIM_TimeBaseStructure.TIM_CounterMode    = TIM_CounterMode_Up;
    TIM_TimeBaseInit(TIM4, &TIM_TimeBaseStructure);
}

/*
 * HC-SR04 获取距离 (阻塞式, 约40ms)
 * 返回: 距离(cm), 0xFFFF=超时/无回波
 * 原理: TRIG发10μs脉冲 → ECHO高电平宽度(μs) / 58 = 距离(cm)
 */
unsigned short HCSR04_GetDistance(void)
{
    unsigned long timeout;

    /* 发送15μs触发脉冲 */
    HCSR04_TRIG = 1;
    Delay(400);            /* ~16μs */
    HCSR04_TRIG = 0;

    /* 等待模块发出8个40kHz脉冲 (~200μs), 超时40ms */
    Delay(5000);

    /* 等待ECHO上升沿 */
    timeout = 0;
    while(!HCSR04_ECHO)
    {
        if(++timeout > 500000) return 0xFFFF;
    }

    /* 启动TIM4计时, 测量ECHO高电平宽度 */
    TIM_SetCounter(TIM4, 0);
    TIM_Cmd(TIM4, ENABLE);

    /* 等待ECHO下降沿 */
    timeout = 0;
    while(HCSR04_ECHO)
    {
        if(++timeout > 500000)
        {
            TIM_Cmd(TIM4, DISABLE);
            return 0xFFFF;
        }
    }

    TIM_Cmd(TIM4, DISABLE);

    /* pulse_us / 58 = 距离(cm) */
    return (unsigned short)(TIM_GetCounter(TIM4) / 58);
}

void IO_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStructure;

    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOB | RCC_APB2Periph_GPIOA, ENABLE);

    /* PB6=RELAY1, PB7=RELAY2, PB8=SUO(锁) 推挽输出 */
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_6 | GPIO_Pin_7 | GPIO_Pin_8;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_10MHz;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_Out_PP;
    GPIO_Init(GPIOB, &GPIO_InitStructure);

    /* PA11=HCSR04 TRIG(推挽输出), PA12=HCSR04 ECHO(上拉输入) */
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_11;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_10MHz;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_Out_PP;
    GPIO_Init(GPIOA, &GPIO_InitStructure);

    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_12;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_10MHz;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IPU;  /* 内部上拉, 增强抗干扰 */
    GPIO_Init(GPIOA, &GPIO_InitStructure);

    /* 上电默认: 停止, 锁释放, TRIG拉低 */
    PBout(6)=0;
    PBout(7)=0;
    PBout(8)=0;
    HCSR04_TRIG = 0;
}

void LIMIT_SW_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStructure;

    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOB, ENABLE);

    /* PB4=限位下, PB5=限位上 下拉输入 */
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_4 | GPIO_Pin_5;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_10MHz;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IPD;
    GPIO_Init(GPIOB, &GPIO_InitStructure);
}

/* 推杆控制 */
void Motor_Control(unsigned char *pDir)
{
    switch(*pDir)
    {
    case Close_PB4:
        if(LIMIT_SW1)
        {
            RELAY1 = 0;
            RELAY2 = 0;
        }
        else
        {
            RELAY1 = 0;
            RELAY2 = 1;
        }
        break;

    case Open_PB5:
        if(LIMIT_SW2)
        {
            RELAY1 = 0;
            RELAY2 = 0;
        }
        else
        {
            RELAY1 = 1;
            RELAY2 = 0;
        }
        break;

    case DIR_STOP:
    default:
        RELAY1 = 0;
        RELAY2 = 0;
        break;
    }
}

/*
 * 称重状态机处理
 * WEIGH_IDLE:  空闲, page6 随 tick 更新当前总重
 * WEIGH_ACTIVE: 收到03后进入, 持续计算 垃圾重量 = 当前总重 - 投放前总重,
 *               检测到稳定垃圾重量时发送 page7 显示重量和总价
 */
void chengzhing(unsigned char *pir)
{
    unsigned long  diff;

    switch(*pir)
    {
    case WEIGH_ACTIVE:
    {
        unsigned long  garbage;

        /* 垃圾重量 = 当前总重 - 投放前总重 (防下溢) */
        if(g_weight > baseline_total)
            garbage = g_weight - baseline_total;
        else
            garbage = 0;

        /* 稳定性检测: 连续STABLE_COUNT次波动≤STABLE_THRESHOLD */
        diff = (garbage > stable_garbage)
            ? (garbage - stable_garbage)
            : (stable_garbage - garbage);

        if(diff <= STABLE_THRESHOLD)
        {
            garbage_stable_cnt++;
            if(garbage_stable_cnt >= STABLE_COUNT
               && garbage > 0
               && garbage != stable_garbage)
            {
                stable_garbage = garbage;
                garbage_stable_cnt = 0;
            }
        }
        else
        {
            garbage_stable_cnt = 0;
            stable_garbage = garbage;
        }

                /* page6: only refresh on new weight data */
        if(g_weight_updated)
        {
            g_weight_updated = 0;
            UART3_SendScreenVal("page6.n1.val=", kg);
            UART3_SendScreenVal("page6.n3.val=", bg);
        }
        break;
    }

    case WEIGH_RESULT:
        /* 结果显示中: 不发page6, page7 已在04时发送, 等待02结束 */
        break;

    case WEIGH_IDLE:
    default:
        /* 空闲时不做任何事, page6 由 main loop 在重量更新时发送 */
        break;
    }
}

/*
 * 视觉模块协议解析 (USART1接收)
 * 帧格式: [帧头] [数据] [帧头]
 * AA + data + AA: 推杆控制 (00=开盖, 01=关盖)
 * BB + data + BB: 垃圾单价 (0~9)
 */
static unsigned char Firmware_ExecutionFlags(
    unsigned char cmdDir,
    unsigned char current_weigh_state)
{
    McuUpdateExecutionState state;

    state.delivery_active = delivery_flow_active;
    state.cleaning_state = cleaning_state;
    state.command_direction = cmdDir;
    state.weigh_state = current_weigh_state;
    state.relay1 = RELAY1;
    state.relay2 = RELAY2;
    state.lock_output = SUO;
    state.update_latched = update_prepared;
    return McuUpdateExecution_Flags(&state);
}

static unsigned char Firmware_ExecuteUpdatePrepare(
    unsigned char *pCmdDir,
    unsigned char *pWeighState)
{
    McuUpdateExecutionState state;
    unsigned char flags;

    state.delivery_active = delivery_flow_active;
    state.cleaning_state = cleaning_state;
    state.command_direction = *pCmdDir;
    state.weigh_state = *pWeighState;
    state.relay1 = RELAY1;
    state.relay2 = RELAY2;
    state.lock_output = SUO;
    state.update_latched = update_prepared;
    McuUpdateExecution_Apply(&state);

    /*
     * The Orange Pi owns admission.  Once it sends F2/02, the MCU must execute
     * the stop-and-latch transition even if a local flow had still been active.
     * Latch first so later local input cannot turn an output back on.
     */
    update_prepared = state.update_latched;
    delivery_flow_active = state.delivery_active;
    cleaning_state = state.cleaning_state;
    lock_timer_ticks = 0;
    *pCmdDir = state.command_direction;
    *pWeighState = state.weigh_state;
    RELAY1 = state.relay1;
    RELAY2 = state.relay2;
    SUO = state.lock_output;
    UART3_RxLen = 0;

    flags = Firmware_ExecutionFlags(*pCmdDir, *pWeighState);
    return flags == MCU_UPDATE_ALL_FLAGS
        ? MCU_UPDATE_EXECUTION_OK
        : MCU_UPDATE_EXECUTION_INTERNAL;
}

void Vision_Process(unsigned char *pCmdDir, unsigned char *pPrice,
                    unsigned char *pWeighState)
{
        /* A0 frame (195 bytes) */
    if(Vision_RxLen>=195 && Vision_RxBuf[0]==0xA0 && Vision_RxBuf[194]==0xA0)
    {
        unsigned char url_len_rx=Vision_RxBuf[1],j;
        if(!update_prepared && url_len_rx>=1 && url_len_rx<=192)
        {
            url_len=url_len_rx;
            for(j=0;j<url_len_rx;j++)url_buffer[j]=Vision_RxBuf[2+j];
            url_buffer[url_len_rx]=0;
            UART3_SendQRCode((char*)url_buffer);
        }
        Vision_RxLen-=195;
        if(Vision_RxLen>0)
        {
            unsigned char i;
            USART_ITConfig(USART1, USART_IT_RXNE, DISABLE);   /* 防搬移时新字节插入 */
            for(i=0;i<Vision_RxLen;i++)Vision_RxBuf[i]=Vision_RxBuf[i+195];
            USART_ITConfig(USART1, USART_IT_RXNE, ENABLE);
        }
    }
    
    while(Vision_RxLen >= 3)
    {
        unsigned char header = Vision_RxBuf[0];
        unsigned char data, tail;

        /* A0=195 bytes, don't parse as 3-byte frame */
        if(header == 0xA0)
            break;

        data = Vision_RxBuf[1];
        tail = Vision_RxBuf[2];

        if(header == tail)
        {
            switch(header)
            {
            case 0xAA:   /* 投递流程 (protocol 6.1) */
                if(!update_prepared && data == 0x01)
                {
                    delivery_pre_weight = g_weight;
                    delivery_flow_active = 1;
                    *pCmdDir = Open_PB5;
                    UART3_SendPage("page4");
                }
                else if(!update_prepared && data == 0x00) *pCmdDir = Close_PB4;  /* debug */
                break;
            case 0xBB:   /* 垃圾单价 */
                if(!update_prepared && data <= 9)
                {
                    *pPrice = data;
                    UART3_SendScreenVal("page0.n0.val=", unit_price);
                    UART3_SendScreenVal("page3.n0.val=", unit_price);
                    UART3_SendScreenVal("page7.n0.val=", unit_price);
                }
                break;
            case 0xEE:   /* 清运流程 (protocol 6.3) */
                if(!update_prepared && data == 0x01)
                {
                    cleaning_pre_weight = g_weight;
                    SUO = 1;
                    cleaning_state = CLEAN_LOCK_ON;
                    lock_timer_ticks = 0;
                    UART3_SendPage("page1");
                }
                else if(!update_prepared && data == 0x00)
                {
                    SUO=0;
                    cleaning_state=CLEAN_IDLE;
                }  /* deprecated */
                break;
            
            case 0xF0:   /* Self-test query -> F1 */
                if(Vision_RxBuf[1] == 0x01)
                {
                    unsigned short dist = HCSR04_GetDistance();
                    unsigned char valid=0x03, smoke_st;
                    unsigned char full_flag=(dist==0xFFFF)?0x00:(dist<OVERFLOW_DIST)?0x01:0x00;
                    smoke_st = SmokeMonitor_GetState();
                    if(g_weight==0&&!baseline_inited)valid&=~0x01;
                    Vision_SendSelfTestResult(valid,g_weight,full_flag,smoke_st);
                }
                break;
            case 0xF2:   /* firmware identity / execute update preparation */
                if(data == 0x01)
                {
                    Vision_SendFirmwareStatus(
                        0x01, 0x00,
                        Firmware_ExecutionFlags(
                            *pCmdDir, *pWeighState));
                }
                else if(data == 0x02)
                {
                    unsigned char status =
                        Firmware_ExecuteUpdatePrepare(
                            pCmdDir, pWeighState);
                    Vision_SendFirmwareStatus(
                        0x02, status,
                        Firmware_ExecutionFlags(
                            *pCmdDir, *pWeighState));
                }
                break;
            }
            /* 消费3字节 */
            Vision_RxLen -= 3;
            if(Vision_RxLen > 0)
            {
                unsigned char i;
                USART_ITConfig(USART1, USART_IT_RXNE, DISABLE);   /* 防搬移时新字节插入 */
                for(i = 0; i < Vision_RxLen; i++)
                    Vision_RxBuf[i] = Vision_RxBuf[i + 3];
                USART_ITConfig(USART1, USART_IT_RXNE, ENABLE);
            }
        }
        else
        {
            /* 帧头不匹配: 丢弃1字节 */
            Vision_RxLen--;
            if(Vision_RxLen > 0)
            {
                unsigned char i;
                USART_ITConfig(USART1, USART_IT_RXNE, DISABLE);   /* 防搬移时新字节插入 */
                for(i = 0; i < Vision_RxLen; i++)
                    Vision_RxBuf[i] = Vision_RxBuf[i + 1];
                USART_ITConfig(USART1, USART_IT_RXNE, ENABLE);
            }
        }
    }
}

/* UART2/UART3 中断优先级配置 */
void NVIC_Configuration(void)
{
    NVIC_InitTypeDef NVIC_InitStructure;

    NVIC_PriorityGroupConfig(NVIC_PriorityGroup_0);

    NVIC_InitStructure.NVIC_IRQChannel = USART2_IRQn;
    NVIC_InitStructure.NVIC_IRQChannelSubPriority = 0;
    NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;
    NVIC_Init(&NVIC_InitStructure);

    NVIC_InitStructure.NVIC_IRQChannel = USART3_IRQn;
    NVIC_InitStructure.NVIC_IRQChannelSubPriority = 1;
    NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;
    NVIC_Init(&NVIC_InitStructure);

    NVIC_InitStructure.NVIC_IRQChannel = USART1_IRQn;
    NVIC_InitStructure.NVIC_IRQChannelSubPriority = 2;
    NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;
    NVIC_Init(&NVIC_InitStructure);
}


int main(void)
{
    unsigned long  weight = 0;
    unsigned char ret;
    unsigned char cmdDir = DIR_STOP;
    unsigned char prev_cmdDir = DIR_STOP;    /* 记录上次推杆状态, 用于检测变化 */

    SystemInit();
    IO_Init();
    LED_GPIO_Config();
    NVIC_Configuration();
    USART1_Init();
    USART1_RX_IntEnable(); /* 使能视觉模块接收中断 */
    USART2_int();          /* RS485 仅用于采集重量 */
    USART3_Init();         /* UART3 串口屏幕通信 */
    LIMIT_SW_Init();
    ADC1_Init();        /* PA4 MQ-2烟雾传感器 */
    TIM3_Init();        /* 100ms定时器, 驱动重量采集 */
    HCSR04_Init();      /* HC-SR04超声波溢满检测 */
    SmokeMonitor_Init();


    while (1)
    {
		
        /* ========== UART3 命令处理 ========== */
			        /* ========== 视觉模块命令处理 (USART1) ========== */
        Vision_Process(&cmdDir, &unit_price, &weigh_state);
        if(update_prepared)
        {
            cmdDir = DIR_STOP;
            weigh_state = WEIGH_IDLE;
            RELAY1 = 0;
            RELAY2 = 0;
            SUO = 0;
            UART3_RxLen = 0;
        }
        while(!update_prepared && UART3_RxLen > 0)
        {
            unsigned char consumed = 0;
            unsigned char matched  = 0;

            switch(UART3_RxBuf[0])
            {
            case 0x01:   /* 开盖 */
                cmdDir = Open_PB5;
//                UART3_SendByte(0xC1); UART3_SendByte(0xC1);
                matched = 1; consumed = 1;
                break;
            case 0x00:   /* 关盖 */
                cmdDir = Close_PB4;
//                UART3_SendByte(0xC3); UART3_SendByte(0xF0);
                matched = 1; consumed = 1;
                break;
            case 0x03:   /* 开始测重: 进入测重模式, 不清零baseline(由02统一更新) */
                weigh_state = WEIGH_ACTIVE;
                stable_garbage = 0;
                garbage_stable_cnt = 0;
                matched = 1; consumed = 1;
                break;
            case 0x04:   /* 显示结果: 发送稳定垃圾重量+总价到 page7 */
            {
                unsigned short d_kg, d_bg, d_yuan, d_jiao, d_fen;
                unsigned long  d_total;
								cmdDir = Close_PB4;
                d_kg    = stable_garbage / 1000;
                d_bg    = stable_garbage % 1000 / 100;
                d_total = (unsigned long)unit_price * stable_garbage/10;
							
                d_yuan  = d_kg*unit_price/10+(d_kg*unit_price%10+d_bg*unit_price/10)/10;
                d_jiao  = (d_kg*unit_price%10+d_bg*unit_price/10)%10;								
                d_fen   =d_bg*unit_price%10 ;
								weigh_state = WEIGH_RESULT;  /* 停止page6发送 */
                UART3_SendScreenVal("page7.n1.val=", d_kg);
                UART3_SendScreenVal("page7.n3.val=", d_bg);
                UART3_SendScreenVal("page7.n2.val=", d_yuan);
                UART3_SendScreenVal("page7.n4.val=", d_jiao);
                UART3_SendScreenVal("page7.n5.val=", d_fen);
               //	baseline_total = 0;//清零前
                matched = 1; consumed = 1;
                break;
            }
            case 0x02:   /* 测重结束: 保存基准, 发送溢满检测+重量结果 */
            {
                unsigned long post_w=g_weight;

                weigh_state = WEIGH_IDLE;

                /* DD完成帧：仅活跃投递使用已保存的投前重量，0g也是合法重量。 */
                if(delivery_flow_active)
                {
                    unsigned long pre_w;
                    unsigned short dist=HCSR04_GetDistance();
                    unsigned char full_byte;
                    pre_w=delivery_pre_weight;
                    full_byte=(dist==0xFFFF)?0x00:(dist<OVERFLOW_DIST)?0x01:0x00;
                    Vision_SendDeliveryResult(pre_w,post_w,full_byte);
                }

                baseline_total=post_w;
                baseline_inited=1;
                delivery_flow_active=0;
                delivery_pre_weight=0;

							matched = 1; consumed = 1;
                break;
            }
						 case 0x06:   /* continue: no report, keep pre-weight */
                weigh_state = WEIGH_IDLE;
                matched = 1; consumed = 1;
                break;
            case 0x05:   /* 清运完成: send EF */
            {
                if(cleaning_state == CLEAN_WAIT_CONFIRM)
                {
                    unsigned long pre_w,post_w;
                    unsigned short dist=HCSR04_GetDistance();
                    unsigned char full_byte;
                    pre_w=cleaning_pre_weight;
                    post_w=g_weight;
                    full_byte=(dist==0xFFFF)?0x00:(dist<OVERFLOW_DIST)?0x01:0x00;
                    Vision_SendCleaningResult(pre_w,post_w,full_byte);
                    cleaning_state=CLEAN_IDLE;
                    cleaning_pre_weight=0;
                    baseline_total=g_weight;   /* 清运后立即刷新基准 */
                    baseline_inited=1;
                }
                matched = 1; consumed = 1;
                break;
            }
						case 0x07:
							{
								SUO = 1; lock_timer_ticks = 0;
								 matched = 1; consumed = 1;
									break;
							}
            }  /* end switch */



            if(!matched)
            {
                /* 未知字节: 丢弃, 防止卡死缓冲区 */
                consumed = 1;
            }

            /* 将剩余字节前移 */
            if(UART3_RxLen > consumed)
            {
                unsigned char i;
                USART_ITConfig(USART3, USART_IT_RXNE, DISABLE);
                for(i = 0; i < UART3_RxLen - consumed; i++)
                    UART3_RxBuf[i] = UART3_RxBuf[consumed + i];
                UART3_RxLen -= consumed;
                USART_ITConfig(USART3, USART_IT_RXNE, ENABLE);
            }
            else
            {
                UART3_RxLen = 0;
            }
        }



        /* 推杆控制 */
        Motor_Control(&cmdDir);

        /* 推杆状态变化时发送AA帧 */
        if(cmdDir != prev_cmdDir)
        {
            Vision_SendPushRod((cmdDir == Close_PB4) ? 0x00 : 0x01);
            prev_cmdDir = cmdDir;
        }

                /* SmokeMonitor: debounced state machine */
        SmokeMonitor_Update();
        if(SmokeMonitor_PollChanged())
            Vision_SendSmoke(SmokeMonitor_GetState());


        /* ========== 定时器驱动的非阻塞称重采集 ========== */
        if(g_weight_tick)
        {
            g_weight_tick = 0;
            Weight_Read_Start();
            /* Lock auto-off ~4s */
            if(cleaning_state == CLEAN_LOCK_ON)
            {
                lock_timer_ticks++;
                if(lock_timer_ticks >= 8){SUO=0;cleaning_state=CLEAN_WAIT_CONFIRM;lock_timer_ticks=0;}
            }
}

        ret = Weight_Read_Poll(&weight);

        if(ret == 0)  /* 重量数据就绪 */
        {
          //  // printf("Weight=%d g\r\n", weight);
            g_weight = Weight_Filter(weight);   /* filtered */
            g_weight_updated = 1;

            /* 首次上电: 自动用第一个重量读数初始化基准 */
            if(!baseline_inited)
            {
                baseline_total = g_weight;
                baseline_inited = 1;
            }
            kg = weight / 1000;
            bg = weight % 1000 / 100;
            total_price = (unsigned long)unit_price * weight;
            yuan = total_price / 1000;
            jiao = (total_price / 100) % 10;
            fen  = (total_price / 10) % 10;
        }
        else if(ret == 1)
        {
            /* Weight Timeout */ // printf("Weight Read Timeout!\r\n");
        }
        else if(ret == 3)
        {
            /* Weight CRC Err */ // printf("Weight CRC Error!\r\n");
        }
     

        /* 称重状态机: 每次循环都执行 */
        chengzhing(&weigh_state);

        Delay(50000);  /* ~5ms */
    }
}
