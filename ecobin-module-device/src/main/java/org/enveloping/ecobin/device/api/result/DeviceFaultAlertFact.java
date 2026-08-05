package org.enveloping.ecobin.device.api.result;

import org.enveloping.ecobin.device.api.persistence.DeviceFaultAlertScopeRef;
import java.time.Instant;
import java.util.UUID;

public record DeviceFaultAlertFact(
        DeviceFaultAlertScopeRef scopeRef,
        UUID faultUid,
        String deploymentCode,
        Integer portNo,
        String componentType,
        String faultCode,
        String impactLevel,
        String state,
        Instant firstDetectedAt,
        Instant lastDetectedAt,
        Instant recoveredAt) {
}
