/* Test-only access to the actual ingress-owned measurement engine. */
#include "mcu_work_preparation.h"
__declspec(dllexport) McuWeightRun *TestPreparation_Weight(McuWorkPreparation *owner) { return &owner->weight; }
__declspec(dllexport) McuDeviceFacts *TestPreparation_Facts(McuControlEndpoint *endpoint) { return &endpoint->facts; }
__declspec(dllexport) McuWorkState *TestPreparation_Work(McuControlEndpoint *endpoint) { return &endpoint->work; }
__declspec(dllexport) McuProcessEventSlot *TestPreparation_Process(McuControlEndpoint *endpoint) { return &endpoint->process_event; }
__declspec(dllexport) void TestPreparation_SetEventSequence(McuControlEndpoint *endpoint, uint32_t sequence) {
    endpoint->critical_event_sequence = sequence;
}
