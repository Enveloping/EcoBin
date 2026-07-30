package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;

import java.util.Objects;

public record CurrentMiniappWalletIdentity(
        OrganizationUserUid organizationUserUid,
        DeliveryQueryOrganizationUserRef pendingRewardOwnerRef,
        DeliveryWalletQueryOwnerRef walletOwnerRef) {

    public CurrentMiniappWalletIdentity {
        Objects.requireNonNull(
                organizationUserUid,
                "organizationUserUid");
        Objects.requireNonNull(
                pendingRewardOwnerRef,
                "pendingRewardOwnerRef");
        Objects.requireNonNull(walletOwnerRef, "walletOwnerRef");
    }
}
