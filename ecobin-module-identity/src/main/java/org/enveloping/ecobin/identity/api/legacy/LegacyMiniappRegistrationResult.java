package org.enveloping.ecobin.identity.api.legacy;

public record LegacyMiniappRegistrationResult(
        LegacyOrganizationUserId userId,
        long tenantId,
        String openid,
        String username,
        String realName,
        int role,
        int status,
        String nickname,
        String avatar,
        boolean newRegistration) {
}
