package org.enveloping.ecobin.device.api.result;

import java.time.LocalDateTime;

public record FullnessSamplePersistenceFacts(
        long tenantId,
        long organizationId,
        long deploymentId,
        long portId,
        long detectionId,
        long commandId,
        long edgeEventId,
        long physicalResultId,
        LocalDateTime backendReceivedAt,
        FullnessSamplePhysicalFact physicalFact) {
}
