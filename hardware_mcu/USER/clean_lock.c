#include "clean_lock.h"

void CleanLock_Init(volatile CleanLock *lock)
{
    lock->powered = 0U;
    lock->started_ms = 0U;
    lock->duration_ms = 0U;
}
void CleanLock_Stop(volatile CleanLock *lock) { lock->powered = 0U; }

uint8_t CleanLock_Start(volatile CleanLock *lock, uint32_t now, uint32_t duration_ms)
{
    if(duration_ms == 0U || duration_ms >= 0x80000000UL)
        return 0U;
    lock->powered = 0U;
    lock->started_ms = now;
    lock->duration_ms = duration_ms;
    lock->powered = 1U;
    return 1U;
}

void CleanLock_Update(volatile CleanLock *lock, uint32_t now)
{
    if(lock->powered && (uint32_t)(now - lock->started_ms) >= lock->duration_ms)
        lock->powered = 0U;
}
uint8_t CleanLock_IsPowered(const volatile CleanLock *lock) { return lock->powered; }
