package org.enveloping.ecobin.funds.application.deliveryrevision;

import java.time.LocalDateTime;
import java.util.Optional;
import java.util.UUID;

interface DeliveryRevisionDeltaRepository {

    Optional<WalletRow> lockWallet(
            long tenantId,
            long organizationId,
            long organizationUserId);

    Optional<OrganizationCounterRow> lockOrganizationCounter(
            long tenantId,
            long organizationId);

    Optional<ActiveWithdrawalRow> lockActiveWithdrawal(
            long tenantId,
            long organizationId,
            long walletId);

    Optional<WithdrawalOrderRow> lockWithdrawalOrder(
            long tenantId,
            long organizationId,
            long walletId,
            long withdrawalOrderId);

    long insertWalletEntry(WalletEntryInsert insert);

    void advanceOrganizationCounter(
            OrganizationCounterUpdate update);

    void updateWallet(WalletUpdate update);

    void updateWithdrawalOrder(WithdrawalOrderUpdate update);

    record WalletRow(
            long id,
            long availableBalanceCent,
            long frozenWithdrawalCent,
            long lastEntrySequenceNo,
            String deliveryGateState,
            Long deliveryGateThresholdSnapshotCent,
            Long deliveryGateTriggerEntryId,
            LocalDateTime deliveryGateLatchedAt,
            long lockVersion) {
    }

    record OrganizationCounterRow(
            long lastVisibilitySequenceNo,
            long lockVersion) {
    }

    record ActiveWithdrawalRow(long withdrawalOrderId) {
    }

    record WithdrawalOrderRow(
            long id,
            String businessState,
            boolean negativeBalancePause,
            boolean postBoundaryRisk,
            String preChannelBlockReason,
            LocalDateTime channelBoundaryAt,
            long lockVersion) {
    }

    record WalletEntryInsert(
            UUID entryUid,
            long tenantId,
            long organizationId,
            long walletId,
            long organizationUserId,
            long entrySequenceNo,
            long visibilitySequenceNo,
            String eventType,
            long availableDeltaCent,
            long availableBeforeCent,
            long availableAfterCent,
            long frozenBeforeCent,
            long deliveryRevisionId,
            LocalDateTime occurredAt,
            LocalDateTime createdAt) {
    }

    record OrganizationCounterUpdate(
            long tenantId,
            long organizationId,
            long previousVisibilitySequenceNo,
            long nextVisibilitySequenceNo,
            long expectedLockVersion,
            LocalDateTime updatedAt) {
    }

    record WalletUpdate(
            long tenantId,
            long organizationId,
            long organizationUserId,
            long walletId,
            long previousEntrySequenceNo,
            long nextEntrySequenceNo,
            long availableBalanceCent,
            String deliveryGateState,
            Long deliveryGateThresholdSnapshotCent,
            Long deliveryGateTriggerEntryId,
            LocalDateTime deliveryGateLatchedAt,
            long expectedLockVersion,
            LocalDateTime updatedAt) {
    }

    record WithdrawalOrderUpdate(
            long tenantId,
            long organizationId,
            long walletId,
            long withdrawalOrderId,
            boolean negativeBalancePause,
            boolean postBoundaryRisk,
            long expectedLockVersion,
            LocalDateTime updatedAt) {
    }
}
