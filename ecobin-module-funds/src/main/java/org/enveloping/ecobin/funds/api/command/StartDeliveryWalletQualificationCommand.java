package org.enveloping.ecobin.funds.api.command;

import org.enveloping.ecobin.identity.api.persistence.StartDeliveryWalletOwnerRef;

import java.util.Objects;

/**
 * 使用 recycling 已锁定配置中的负余额开门阈值复核钱包资格。
 */
public record StartDeliveryWalletQualificationCommand(
        StartDeliveryWalletOwnerRef walletOwnerRef,
        long openBalanceFloorCent) {

    public StartDeliveryWalletQualificationCommand {
        Objects.requireNonNull(walletOwnerRef, "walletOwnerRef");
        if (openBalanceFloorCent >= 0) {
            throw new IllegalArgumentException(
                    "openBalanceFloorCent must be negative");
        }
    }
}
