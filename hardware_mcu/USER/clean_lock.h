#ifndef ECOBIN_CLEAN_LOCK_H
#define ECOBIN_CLEAN_LOCK_H
#include <stdint.h>

/* Caller serializes Start/Stop against the timer ISR (see actuator_runtime). */
typedef struct {
    uint32_t started_ms;
    uint32_t duration_ms;
    uint8_t powered;
} CleanLock;
void CleanLock_Init(volatile CleanLock *lock);
uint8_t CleanLock_Start(volatile CleanLock *lock, uint32_t now, uint32_t duration_ms);
void CleanLock_Stop(volatile CleanLock *lock);
void CleanLock_Update(volatile CleanLock *lock, uint32_t now);
uint8_t CleanLock_IsPowered(const volatile CleanLock *lock);
#endif
