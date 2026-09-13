#ifndef ECOBIN_TEST_FAKE_ADC_H
#define ECOBIN_TEST_FAKE_ADC_H
/* Replace only the STM32 ADC boundary; compile the real smoke state machine. */
#define __ADC_H
typedef unsigned short u16;
unsigned char ADC1_TryRead(u16 *value);
#endif
