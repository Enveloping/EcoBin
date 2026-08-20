/******************** (C) COPYRIGHT 2019 Designed by Captain *********************
 * 文件名  ：adc.c
 * 功能    ：ADC1 烟雾传感器 MQ-2 (PA4)
 * 实现平台：STM32F103RCT6
 * MQ-2输出: 0~3.3V模拟电压, 烟雾越浓电压越高
 * 注意    ：MQ-2上电需预热1分钟才能稳定读数
**********************************************************************************/
#include "adc.h"

static void Delay_us(u32 n)
{
    u32 i;
    for(; n > 0; n--)
        for(i = 0; i < 12; i++);  /* 72MHz, ~1us per loop */
}

/* PA4 = ADC1_IN4 */
void ADC1_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStructure;
    ADC_InitTypeDef  ADC_InitStructure;

    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_ADC1, ENABLE);
    RCC_ADCCLKConfig(RCC_PCLK2_Div6);   /* ADC时钟 = 72M/6 = 12MHz */

    /* PA4 - 模拟输入 */
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_4;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AIN;
    GPIO_Init(GPIOA, &GPIO_InitStructure);

    ADC_DeInit(ADC1);
    ADC_InitStructure.ADC_Mode = ADC_Mode_Independent;
    ADC_InitStructure.ADC_ScanConvMode = DISABLE;
    ADC_InitStructure.ADC_ContinuousConvMode = DISABLE;
    ADC_InitStructure.ADC_ExternalTrigConv = ADC_ExternalTrigConv_None;
    ADC_InitStructure.ADC_DataAlign = ADC_DataAlign_Right;
    ADC_InitStructure.ADC_NbrOfChannel = 1;
    ADC_Init(ADC1, &ADC_InitStructure);

    /* 预配置通道4, 采样周期最长(239.5周期)适合高阻抗信号 */
    ADC_RegularChannelConfig(ADC1, ADC_Channel_4, 1, ADC_SampleTime_239Cycles5);

    ADC_Cmd(ADC1, ENABLE);

    /* 校准 */
    ADC_ResetCalibration(ADC1);
    while(ADC_GetResetCalibrationStatus(ADC1));
    ADC_StartCalibration(ADC1);
    while(ADC_GetCalibrationStatus(ADC1));

    /* 空读几次, 让ADC稳定 */
    {
        u8 i;
        for(i = 0; i < 5; i++)
        {
            ADC_SoftwareStartConvCmd(ADC1, ENABLE);
            while(!ADC_GetFlagStatus(ADC1, ADC_FLAG_EOC));
            ADC_GetConversionValue(ADC1);
            Delay_us(200);
        }
    }
}

/* 读PA4 ADC值 (0~4095) */
u16 ADC1_Read(void)
{
    ADC_SoftwareStartConvCmd(ADC1, ENABLE);
    while(!ADC_GetFlagStatus(ADC1, ADC_FLAG_EOC));

    return ADC_GetConversionValue(ADC1);
}

/* 带超时保护的ADC读取: 返回1=成功(0~4095), 0=硬件超时 */
unsigned char ADC1_TryRead(u16 *value)
{
    u16 timeout = 0;

    ADC_SoftwareStartConvCmd(ADC1, ENABLE);
    while(!ADC_GetFlagStatus(ADC1, ADC_FLAG_EOC))
    {
        if(++timeout > 50000)   /* 约500μs超时 */
            return 0;
    }
    *value = ADC_GetConversionValue(ADC1);
    return 1;
}

/* 读烟雾传感器电压 (0~3.3V) */
float Smoke_GetVoltage(void)
{
    u16 adc_val = ADC1_Read();
    return (float)adc_val * 3.3f / 4095.0f;
}
