/* Test-only access to foreground producer-owned state. No alternate logic. */
#include "mcu_control_endpoint.h"
__declspec(dllexport) McuWorkState *TestControl_Work(McuControlEndpoint *endpoint) { return &endpoint->work; }
__declspec(dllexport) McuSession *TestControl_Session(McuControlEndpoint *endpoint) { return &endpoint->session; }
__declspec(dllexport) McuDeviceFacts *TestControl_Facts(McuControlEndpoint *endpoint) { return &endpoint->facts; }
__declspec(dllexport) McuProcessEventSlot *TestControl_ProcessEvent(McuControlEndpoint *endpoint) { return &endpoint->process_event; }
/* Boundary injection only: avoid issuing 2^32 real calls in exhaustion tests. */
__declspec(dllexport) void TestControl_SetEventSequence(McuControlEndpoint *endpoint, uint32_t sequence) {
    endpoint->critical_event_sequence = sequence;
}
