package org.enveloping.ecobin.funds.api.result;

import org.enveloping.ecobin.funds.api.id.WalletEntryUid;

import java.util.Objects;

/**
 * 投递修订差额已经原子写入钱包后的结果。
 */
public record AppliedDeliveryWalletDelta(
        long beforeBalanceCent,
        long afterBalanceCent,
        long deltaCent,
        WalletEntryUid entryUid,
        DeliveryGateEffect gateEffect,
        WithdrawalBalanceEffect withdrawalEffect) {

    public AppliedDeliveryWalletDelta {
        Objects.requireNonNull(entryUid, "entryUid");
        Objects.requireNonNull(gateEffect, "gateEffect");
        Objects.requireNonNull(
                withdrawalEffect,
                "withdrawalEffect");
        if (deltaCent == 0) {
            throw new IllegalArgumentException(
                    "deltaCent must not be zero");
        }
        if (Math.addExact(beforeBalanceCent, deltaCent)
                != afterBalanceCent) {
            throw new IllegalArgumentException(
                    "before balance plus delta must equal after balance");
        }
    }
}
