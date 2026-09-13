#include <assert.h>
#include "clean_lock.h"

int main(void)
{
    CleanLock lock;
    CleanLock_Init(&lock);
    assert(!CleanLock_IsPowered(&lock));
    assert(CleanLock_Start(&lock, 100U, 4800U));
    CleanLock_Update(&lock, 4899U);
    assert(CleanLock_IsPowered(&lock));
    CleanLock_Update(&lock, 4900U);
    assert(!CleanLock_IsPowered(&lock));
    assert(CleanLock_Start(&lock, UINT32_MAX - 99U, 250U));
    CleanLock_Update(&lock, 149U);
    assert(CleanLock_IsPowered(&lock));
    CleanLock_Update(&lock, 150U);
    assert(!CleanLock_IsPowered(&lock));
    assert(CleanLock_Start(&lock, 200U, 1000U));
    assert(CleanLock_Start(&lock, 300U, 1000U));
    CleanLock_Update(&lock, 1200U);
    assert(CleanLock_IsPowered(&lock));
    CleanLock_Stop(&lock);
    CleanLock_Update(&lock, 1300U);
    assert(!CleanLock_IsPowered(&lock));
    assert(!CleanLock_Start(&lock, 0U, 0x80000000UL));
    return 0;
}
