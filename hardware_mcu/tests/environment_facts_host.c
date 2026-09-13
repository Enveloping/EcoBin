/* Synthetic ADC boundary only; smoke/debounce/facts/clock are production C. */
#include "smoke_monitor.h"
static unsigned short value;
static unsigned char healthy;
static unsigned int reads;
unsigned char ADC1_TryRead(unsigned short *output) {
    reads++;
    *output = value;
    return healthy;
}
void TestEnvironment_Reset(void) { value = 0; healthy = 1; reads = 0; SmokeMonitor_Init(); }
void TestEnvironment_Adc(unsigned short next_value, unsigned char ok) { value = next_value; healthy = ok; }
unsigned int TestEnvironment_Reads(void) { return reads; }
