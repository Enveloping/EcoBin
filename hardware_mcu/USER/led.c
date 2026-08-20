/******************** (C) COPYRIGHT 2019 Designed by Captain *********************
 * 文件名  ：led.c
 * 功能    ：led 应用函数
 *
 * 实现平台：STM32F103RCT6工控板
 * 硬件连接：-----------------
 *          |   PC0 - LED1   |
 *          |   PC1 - LED2   |
 *          |                 |
 *           -----------------
 * 店主    ：踏上电子工作室
 * 淘宝店  ：https://shop151358311.taobao.com/
**********************************************************************************/
#include "led.h"


 /***************  配置LED用到的I/O口 *******************/
void LED_GPIO_Config(void)
{
  GPIO_InitTypeDef GPIO_InitStructure;
  RCC_APB2PeriphClockCmd( RCC_APB2Periph_GPIOB, ENABLE); // 使能PB端口时钟
  GPIO_InitStructure.GPIO_Pin = GPIO_Pin_15 | GPIO_Pin_14;
  GPIO_InitStructure.GPIO_Mode = GPIO_Mode_Out_PP;
  GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
  GPIO_Init(GPIOB, &GPIO_InitStructure);  //初始化PB端口
  GPIO_ResetBits(GPIOB, GPIO_Pin_15 | GPIO_Pin_14); // 关闭所有LED
}
