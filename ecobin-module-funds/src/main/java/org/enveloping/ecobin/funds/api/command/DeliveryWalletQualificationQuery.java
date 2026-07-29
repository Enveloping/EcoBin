package org.enveloping.ecobin.funds.api.command;

import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;

import java.util.Objects;

/**
 * GET 投递展示使用的当前用户钱包资格查询。
 */
public record DeliveryWalletQualificationQuery(
        OrganizationUserUid organizationUserUid,
        long openBalanceFloorCent,
        DeliveryWalletQueryOwnerRef walletQueryOwnerRef) {

    public DeliveryWalletQualificationQuery {
        Objects.requireNonNull(
                organizationUserUid,
                "organizationUserUid");
        Objects.requireNonNull(
                walletQueryOwnerRef,
                "walletQueryOwnerRef");
        if (openBalanceFloorCent >= 0) {
            throw new IllegalArgumentException(
                    "openBalanceFloorCent must be negative");
        }
    }
}
