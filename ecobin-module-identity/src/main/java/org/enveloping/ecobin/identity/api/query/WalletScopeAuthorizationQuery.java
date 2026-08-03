package org.enveloping.ecobin.identity.api.query;

import java.util.UUID;

/**
 * Explicit organization target for wallet Web authorization.
 */
public record WalletScopeAuthorizationQuery(
        boolean platformPath,
        String tenantCode,
        String organizationCode,
        UUID organizationUserUid,
        boolean directSummaryForAdjustment) {

    public WalletScopeAuthorizationQuery(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid) {
        this(platformPath, tenantCode, organizationCode,
                organizationUserUid, false);
    }

    public WalletScopeAuthorizationQuery {
        organizationCode = requiredCode(
                organizationCode,
                "organizationCode");
        if (platformPath) {
            tenantCode = requiredCode(tenantCode, "tenantCode");
        } else if (tenantCode != null) {
            throw new IllegalArgumentException(
                    "staff wallet access must derive tenant from session");
        }
        if (directSummaryForAdjustment
                && organizationUserUid == null) {
            throw new IllegalArgumentException(
                    "wallet adjustment summary requires an exact user");
        }
    }

    private static String requiredCode(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(
                    name + " must not be blank");
        }
        return value.trim();
    }
}
