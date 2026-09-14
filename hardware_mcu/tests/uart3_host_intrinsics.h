#ifndef ECOBIN_UART3_HOST_INTRINSICS_H
#define ECOBIN_UART3_HOST_INTRINSICS_H
#include <stdint.h>
uint32_t HostGetPrimask(void);
void HostDisableIrq(void);
void HostSetPrimask(uint32_t value);
#endif
