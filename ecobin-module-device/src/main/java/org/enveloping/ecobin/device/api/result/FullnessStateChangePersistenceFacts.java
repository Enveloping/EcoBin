package org.enveloping.ecobin.device.api.result;

import java.time.LocalDateTime;

public record FullnessStateChangePersistenceFacts(
        long tenantId,
        long organizationId,
        long assetId,
        long portId,
        long edgeEventId,
        long stateFactId,
        Long sourceDeliverySessionId,
        LocalDateTime backendReceivedAt,
        FullnessStateChangePhysicalFact physicalFact) {
}
