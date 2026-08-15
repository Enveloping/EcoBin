package org.enveloping.ecobin.identity.application.platformminiapp;

import java.time.Instant;
import java.util.Set;
import java.util.UUID;

public record PlatformMiniappActor(
        long factoryOperatorId,
        UUID factoryOperatorUid,
        String operatorCode,
        UUID sessionUid,
        long authVersion,
        Instant expiresAt,
        String displayName,
        Set<String> capabilities) {

    public PlatformMiniappActor {
        capabilities = Set.copyOf(capabilities);
    }
}
