package org.enveloping.ecobin.funds.api.result;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

public record WalletAdjustmentResult(
        UUID adjustmentUid,
        UUID entryUid,
        long deltaCent,
        long availableBalanceBeforeCent,
        long availableBalanceAfterCent,
        long walletVersion,
        String deliveryGate,
        WalletAdjustmentWithdrawalEffect activeWithdrawalEffect,
        Instant occurredAt) {

    public WalletAdjustmentResult {
        Objects.requireNonNull(adjustmentUid, "adjustmentUid");
        Objects.requireNonNull(entryUid, "entryUid");
        Objects.requireNonNull(deliveryGate, "deliveryGate");
        Objects.requireNonNull(activeWithdrawalEffect,
                "activeWithdrawalEffect");
        Objects.requireNonNull(occurredAt, "occurredAt");
    }
}
