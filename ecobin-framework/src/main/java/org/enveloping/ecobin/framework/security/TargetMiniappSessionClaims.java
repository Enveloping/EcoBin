package org.enveloping.ecobin.framework.security;

import org.enveloping.ecobin.framework.context.TrustedAudience;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

public record TargetMiniappSessionClaims(
        UUID principalUid,
        UUID sessionUid,
        TrustedAudience audience,
        Instant issuedAt,
        Instant expiresAt) {

    public TargetMiniappSessionClaims {
        Objects.requireNonNull(principalUid, "principalUid");
        Objects.requireNonNull(sessionUid, "sessionUid");
        Objects.requireNonNull(audience, "audience");
        Objects.requireNonNull(issuedAt, "issuedAt");
        Objects.requireNonNull(expiresAt, "expiresAt");
        if (audience != TrustedAudience.MINIAPP
                && audience != TrustedAudience.MINIAPP_STAFF) {
            throw new IllegalArgumentException(
                    "claims require a miniapp audience");
        }
        if (!expiresAt.isAfter(issuedAt)) {
            throw new IllegalArgumentException(
                    "expiresAt must be after issuedAt");
        }
    }
}
