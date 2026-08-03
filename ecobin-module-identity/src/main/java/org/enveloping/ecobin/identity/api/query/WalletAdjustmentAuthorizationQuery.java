package org.enveloping.ecobin.identity.api.query;

import java.util.UUID;

/** Exact Web target for a wallet.adjust command. */
public record WalletAdjustmentAuthorizationQuery(
        boolean platformPath,
        String tenantCode,
        String organizationCode,
        UUID organizationUserUid) {

    public WalletAdjustmentAuthorizationQuery {
        organizationCode = required(organizationCode, "organizationCode");
        organizationUserUid = java.util.Objects.requireNonNull(
                organizationUserUid, "organizationUserUid");
        if (platformPath) {
            tenantCode = required(tenantCode, "tenantCode");
        } else if (tenantCode != null) {
            throw new IllegalArgumentException(
                    "staff wallet adjustment derives tenant from session");
        }
    }

    private static String required(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(name + " must not be blank");
        }
        return value.trim();
    }
}
