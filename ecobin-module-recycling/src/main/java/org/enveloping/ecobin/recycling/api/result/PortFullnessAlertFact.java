package org.enveloping.ecobin.recycling.api.result;

import org.enveloping.ecobin.recycling.api.persistence.PortFullnessAlertScopeRef;
import java.time.Instant;
import java.util.UUID;

public record PortFullnessAlertFact(
        PortFullnessAlertScopeRef scopeRef,
        String deviceCode,
        int portNo,
        String state,
        UUID stateChangeUid,
        Instant reportedAt) {
}
