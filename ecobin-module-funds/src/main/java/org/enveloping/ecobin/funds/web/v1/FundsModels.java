package org.enveloping.ecobin.funds.web.v1;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class FundsModels {

    private FundsModels() {
    }

    public record CreateRechargeRequest(String grossAmountYuan) {
    }

    public record RechargeView(
            String operationId,
            String resourceId,
            String rechargeNo,
            String status,
            long version,
            String grossAmountYuan,
            String feeYuan,
            String netAmountYuan,
            String paymentPreparationStatus,
            String qrCodeUrl,
            Instant expiresAt,
            String statusUrl,
            int recommendedPollAfterMs,
            Instant createdAt,
            Instant paidAt,
            Instant postedAt) {
    }

    public record RechargePage(
            List<RechargeView> items,
            Instant asOf,
            String nextCursor) {

        public RechargePage {
            items = List.copyOf(items);
        }
    }

    public record PayoutAccountView(
            String availablePayoutYuan,
            String frozenWithdrawalYuan,
            String totalPayoutYuan,
            String cumulativeRechargeGrossYuan,
            String cumulativeRechargeFeeYuan,
            String cumulativeRechargeNetYuan,
            String cumulativeSuccessfulWithdrawalYuan,
            String merchantBindingStatus,
            String payoutGateStatus,
            long version,
            Instant asOf) {
    }

    public record PayoutEntryView(
            String entryUid,
            String entryType,
            String availableDeltaYuan,
            String frozenDeltaYuan,
            String availableAfterYuan,
            String frozenAfterYuan,
            String sourceNo,
            Instant occurredAt) {
    }

    public record PayoutEntryPage(
            List<PayoutEntryView> items,
            Instant asOf,
            String nextCursor) {

        public PayoutEntryPage {
            items = List.copyOf(items);
        }
    }

    public record WithdrawalConfigurationView(
            long versionNo,
            String hardLimitYuan,
            String manualMinimumYuan,
            String manualMaximumYuan,
            String manualReviewFreeThresholdYuan,
            Instant publishedAt) {
    }

    public record ReleaseWithdrawalConfigurationRequest(
            Long expectedCurrentVersion,
            String hardLimitYuan,
            String manualMinimumYuan,
            String manualMaximumYuan) {
    }

    public record CreateWithdrawalRequest(String amountYuan) {
    }

    public record EmptyMerchantTransferAuthorizationRequest() {
    }

    public record MerchantTransferAuthorizationView(
            String status,
            String authorizationNo,
            String appId,
            String mchId,
            String packageInfo,
            boolean confirmationRequired,
            Instant confirmationExpiresAt,
            Instant authorizedAt,
            Instant closedAt,
            String closeReason,
            Instant lastSuccessfulQueryAt) {
    }

    public record MerchantTransferAuthorizationAcceptedView(
            String authorizationNo,
            String status,
            String statusUrl,
            int recommendedPollAfterMs) {
    }

    public record ReviewWithdrawalRequest(
            Long expectedVersion,
            String decision,
            String note) {
    }

    public record VersionedWithdrawalRequest(Long expectedVersion) {
    }

    public record WithdrawalView(
            String withdrawalNo,
            String status,
            long version,
            String amountYuan,
            String collectionMode,
            String channelState,
            String channelErrorCode,
            String channelStatusMessage,
            boolean confirmationRequired,
            boolean cancellable,
            boolean channelBoundaryCrossed,
            boolean negativeBalancePaused,
            boolean postBoundaryRisk,
            Instant createdAt,
            Instant reviewedAt,
            Instant endedAt) {
    }

    public record WithdrawalPage(
            List<WithdrawalView> items,
            Instant asOf,
            String nextCursor) {

        public WithdrawalPage {
            items = List.copyOf(items);
        }
    }

    public record MerchantTransferConfirmationView(
            String withdrawalNo,
            String appId,
            String mchId,
            String packageInfo,
            String channelState) {
    }

    public record PayoutGateView(
            String merchantId,
            String status,
            long version,
            UUID pausedEventUid,
            Instant pausedAt) {
    }

    public record RestorePayoutGateRequest(
            Long expectedGateVersion,
            UUID pausedEventUid,
            Boolean fundsReplenishedConfirmed,
            String reason) {
    }

    public record MerchantBindingView(
            String status,
            String appId,
            long miniappVersion,
            Long bindingVersion,
            String merchantId,
            Instant verifiedAt,
            Instant disabledAt) {
    }

    public record VerifyMerchantBindingRequest(
            Long expectedMiniappVersion,
            Long expectedBindingVersion,
            String note) {
    }

    public record DisableMerchantBindingRequest(
            Long expectedBindingVersion,
            String reason) {
    }
}
