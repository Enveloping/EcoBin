package org.enveloping.ecobin.identity.application.directory;

import java.time.Instant;

public record MiniappConfigurationMetadata(
        long id,
        long tenantId,
        long organizationId,
        String appId,
        String displayName,
        boolean loginEnabled,
        String appSecret,
        String entryBaseUrl,
        Instant activatedAt,
        long version,
        Instant configuredAt,
        Instant updatedAt) {

    public boolean activated() {
        return activatedAt != null;
    }

    public boolean appSecretConfigured() {
        return appSecret != null && !appSecret.isBlank();
    }
}
