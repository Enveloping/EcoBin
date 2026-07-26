package org.enveloping.ecobin.framework.security;

import org.enveloping.ecobin.framework.context.TrustedAudience;

import java.time.Instant;
import java.util.UUID;

/**
 * Target Web tokens contain identity only. Tenant, organization and
 * capabilities are resolved from the persisted session and current IAM facts.
 */
public record TargetWebSessionClaims(
        UUID principalUid,
        UUID sessionUid,
        TrustedAudience audience,
        Instant issuedAt,
        Instant expiresAt) {
}
