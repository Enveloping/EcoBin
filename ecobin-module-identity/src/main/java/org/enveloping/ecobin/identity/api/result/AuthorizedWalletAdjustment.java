package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.persistence.WalletAdjustmentScopeRef;
import org.enveloping.ecobin.identity.api.persistence.WalletAdjustmentTargetRef;

import java.util.Objects;
import java.util.UUID;

/** Public actor facts plus two independently consumable opaque persistence refs. */
public record AuthorizedWalletAdjustment(
        boolean platformActor,
        UUID principalUid,
        UUID sessionUid,
        String actorDisplayName,
        String tenantCode,
        String organizationCode,
        UUID organizationUserUid,
        WalletAdjustmentScopeRef configurationScopeRef,
        WalletAdjustmentTargetRef fundsTargetRef) {

    public AuthorizedWalletAdjustment {
        Objects.requireNonNull(principalUid, "principalUid");
        Objects.requireNonNull(sessionUid, "sessionUid");
        Objects.requireNonNull(actorDisplayName, "actorDisplayName");
        Objects.requireNonNull(tenantCode, "tenantCode");
        Objects.requireNonNull(organizationCode, "organizationCode");
        Objects.requireNonNull(organizationUserUid, "organizationUserUid");
        Objects.requireNonNull(configurationScopeRef,
                "configurationScopeRef");
        Objects.requireNonNull(fundsTargetRef, "fundsTargetRef");
    }
}
