package org.enveloping.ecobin.identity.application.miniapp;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public record TargetMiniappActor(
        TrustedPrincipalKind principalKind,
        TrustedAudience audience,
        long principalId,
        UUID principalUid,
        long tenantId,
        String tenantCode,
        long organizationId,
        String organizationCode,
        String organizationName,
        long organizationMiniappId,
        String appId,
        long organizationUserId,
        Long staffMiniappBindingId,
        UUID sessionUid,
        long authVersion,
        Instant expiresAt,
        String entryMode,
        String displayName,
        List<String> capabilities,
        String phoneE164,
        Instant phoneBoundAt) {

    public TargetMiniappActor {
        capabilities = List.copyOf(capabilities);
    }

    public boolean phoneBound() {
        return phoneE164 != null && phoneBoundAt != null;
    }
}
