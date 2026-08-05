package org.enveloping.ecobin.identity.api.result;

import java.util.Map;
import java.util.UUID;

public record GovernanceIdentityFacts(Map<UUID, Entry> entries) {
    public GovernanceIdentityFacts { entries = Map.copyOf(entries); }

    public record Entry(
            String tenantCode,
            String organizationCode,
            UUID actorUid) { }
}
