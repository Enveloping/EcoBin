package org.enveloping.ecobin.recycling.web.v1;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class WalletModels {

    private WalletModels() {
    }

    public record WalletSummary(
            long walletVersion,
            String pendingRewardYuan,
            String availableBalanceYuan,
            String withdrawalProcessingYuan,
            Instant asOf) {
    }

    public record WalletEntryPage<T>(
            List<T> items,
            Instant asOf,
            String nextCursor) {

        public WalletEntryPage {
            items = List.copyOf(items);
        }
    }

    public record PersonalWalletEntry(
            UUID entryUid,
            long entrySequenceNo,
            String entryType,
            String availableDeltaYuan,
            String processingDeltaYuan,
            String availableBalanceAfterYuan,
            String withdrawalProcessingAfterYuan,
            String sourceType,
            String sourceNo,
            Instant occurredAt) {
    }

    public record OrganizationWalletEntry(
            UUID entryUid,
            UUID organizationUserUid,
            long entrySequenceNo,
            String entryType,
            String availableDeltaYuan,
            String processingDeltaYuan,
            String availableBalanceAfterYuan,
            String withdrawalProcessingAfterYuan,
            String sourceType,
            String sourceNo,
            Instant occurredAt) {
    }

    public record MiniappWalletEntry(
            UUID entryUid,
            long entrySequenceNo,
            String entryType,
            String availableDeltaYuan,
            String processingDeltaYuan,
            String availableBalanceAfterYuan,
            String withdrawalProcessingAfterYuan,
            String sourceType,
            String sourceNo,
            Instant occurredAt) {
    }

    public record AdjustWalletRequest(
            String deltaYuan,
            Long expectedWalletVersion,
            String reason) {
    }

    public record WalletAdjustmentView(
            UUID adjustmentUid,
            UUID entryUid,
            String deltaYuan,
            String availableBalanceBeforeYuan,
            String availableBalanceAfterYuan,
            long walletVersion,
            String deliveryGate,
            String activeWithdrawalEffect,
            Instant occurredAt) {
    }
}
