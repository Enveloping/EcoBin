package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;
import org.enveloping.ecobin.identity.api.persistence.WalletQueryScopeRef;

import java.util.Objects;
import java.util.UUID;

/**
 * Safe wallet-read authorization result.
 */
public record AuthorizedWalletScope(
        boolean platformActor,
        String tenantCode,
        String organizationCode,
        UUID organizationUserUid,
        WalletQueryScopeRef scopeRef,
        DeliveryWalletQueryOwnerRef walletOwnerRef,
        DeliveryQueryOrganizationUserRef pendingRewardOwnerRef) {

    public AuthorizedWalletScope {
        tenantCode = nonBlank(tenantCode, "tenantCode");
        organizationCode = nonBlank(
                organizationCode,
                "organizationCode");
        Objects.requireNonNull(scopeRef, "scopeRef");
        boolean targetPresent = organizationUserUid != null;
        if (targetPresent != (walletOwnerRef != null)
                || targetPresent != (pendingRewardOwnerRef != null)) {
            throw new IllegalArgumentException(
                    "target wallet references must match organization user");
        }
    }

    private static String nonBlank(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(name + " must not be blank");
        }
        return value;
    }
}
