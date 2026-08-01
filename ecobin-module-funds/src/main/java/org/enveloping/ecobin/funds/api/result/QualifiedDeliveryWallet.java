package org.enveloping.ecobin.funds.api.result;

import org.enveloping.ecobin.funds.api.id.WalletUid;

import java.util.Objects;

/**
 * 已加锁且通过本次开始投递资格检查的钱包快照。
 */
public record QualifiedDeliveryWallet(
        WalletUid walletUid,
        long availableBalanceCent,
        long openBalanceFloorCent) {

    public QualifiedDeliveryWallet {
        Objects.requireNonNull(walletUid, "walletUid");
        if (openBalanceFloorCent >= 0) {
            throw new IllegalArgumentException(
                    "openBalanceFloorCent must be negative");
        }
    }
}
