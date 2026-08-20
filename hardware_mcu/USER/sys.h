#ifndef __SYS_H
#define	__SYS_H

#include "stm32f10x.h"

// 位带操作，实现51类似的GPIO控制
// IO口操作宏定义
#define BITBAND(addr, bitnum) ((addr & 0xF0000000)+0x2000000+((addr &0xFFFFF)<<5)+(bitnum<<2))
#define MEM_ADDR(addr)  *((volatile unsigned long  *)(addr))
#define BIT_ADDR(addr, bitnum)   MEM_ADDR(BITBAND(addr, bitnum))

// GPIO ODR 地址
#define GPIOA_ODR_Addr    (GPIOA_BASE+12) //0x0C
#define GPIOB_ODR_Addr    (GPIOB_BASE+12) //0x0C
#define GPIOC_ODR_Addr    (GPIOC_BASE+12) //0x0C
#define GPIOD_ODR_Addr    (GPIOD_BASE+12) //0x0C
#define GPIOE_ODR_Addr    (GPIOE_BASE+12) //0x0C
#define GPIOF_ODR_Addr    (GPIOF_BASE+12) //0x0C
#define GPIOG_ODR_Addr    (GPIOG_BASE+12) //0x0C

// 位带操作，实现51类似的IO控制
// 操作PFout(1)=1 表示PF1输出高电平
#define PAout(n)   BIT_ADDR(GPIOA_ODR_Addr,n)
#define PBout(n)   BIT_ADDR(GPIOB_ODR_Addr,n)
#define PCout(n)   BIT_ADDR(GPIOC_ODR_Addr,n)
#define PDout(n)   BIT_ADDR(GPIOD_ODR_Addr,n)
#define PEout(n)   BIT_ADDR(GPIOE_ODR_Addr,n)
#define PFout(n)   BIT_ADDR(GPIOF_ODR_Addr,n)
#define PGout(n)   BIT_ADDR(GPIOG_ODR_Addr,n)

// GPIO IDR 地址 (输入寄存器)
#define GPIOA_IDR_Addr    (GPIOA_BASE+8) //0x08
#define GPIOB_IDR_Addr    (GPIOB_BASE+8) //0x08
#define GPIOC_IDR_Addr    (GPIOC_BASE+8) //0x08
#define GPIOD_IDR_Addr    (GPIOD_BASE+8) //0x08
#define GPIOE_IDR_Addr    (GPIOE_BASE+8) //0x08
#define GPIOF_IDR_Addr    (GPIOF_BASE+8) //0x08
#define GPIOG_IDR_Addr    (GPIOG_BASE+8) //0x08

// 位带输入操作
// PAin(1) 读取PA1引脚电平
#define PAin(n)   BIT_ADDR(GPIOA_IDR_Addr,n)
#define PBin(n)   BIT_ADDR(GPIOB_IDR_Addr,n)
#define PCin(n)   BIT_ADDR(GPIOC_IDR_Addr,n)
#define PDin(n)   BIT_ADDR(GPIOD_IDR_Addr,n)
#define PEin(n)   BIT_ADDR(GPIOE_IDR_Addr,n)
#define PFin(n)   BIT_ADDR(GPIOF_IDR_Addr,n)
#define PGin(n)   BIT_ADDR(GPIOG_IDR_Addr,n)

#endif /* __SYS_H */
