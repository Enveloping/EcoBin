package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.SessionUid;
import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;

import java.util.Objects;

/**
 * 当前普通用户小程序会话中的公开身份信息。
 */
public record CurrentMiniappDeliveryIdentity(
        String tenantCode,
        String organizationCode,
        OrganizationUserUid organizationUserUid,
        boolean phoneBound,
        SessionUid sessionUid,
        DeliveryQueryOrganizationUserRef deliveryQueryUserRef,
        DeliveryWalletQueryOwnerRef walletQueryOwnerRef) {

    public CurrentMiniappDeliveryIdentity {
        tenantCode = nonBlank(tenantCode, "tenantCode");
        organizationCode = nonBlank(
                organizationCode,
                "organizationCode");
        Objects.requireNonNull(
                organizationUserUid,
                "organizationUserUid");
        Objects.requireNonNull(sessionUid, "sessionUid");
        Objects.requireNonNull(
                deliveryQueryUserRef,
                "deliveryQueryUserRef");
        Objects.requireNonNull(
                walletQueryOwnerRef,
                "walletQueryOwnerRef");
    }

    private static String nonBlank(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(name + " must not be blank");
        }
        return value;
    }
}
