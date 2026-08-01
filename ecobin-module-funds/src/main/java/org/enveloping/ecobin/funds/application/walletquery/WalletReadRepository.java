package org.enveloping.ecobin.funds.application.walletquery;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

interface WalletReadRepository {

    Optional<WalletRow> findWallet(
            long tenantId,
            long organizationId,
            long organizationUserId);

    List<EntryRow> findPersonalEntries(
            long tenantId,
            long organizationId,
            long walletId,
            Long beforeEntrySequenceNo,
            int fetchLimit);

    long currentOrganizationHighWatermark(
            long tenantId,
            long organizationId);

    List<EntryRow> findOrganizationEntries(
            OrganizationPageQuery query);

    record WalletRow(
            long id,
            long availableBalanceCent,
            long frozenWithdrawalCent,
            long lockVersion) {
    }

    record EntryRow(
            UUID entryUid,
            UUID organizationUserUid,
            long entrySequenceNo,
            String eventType,
            long availableDeltaCent,
            long frozenDeltaCent,
            long availableAfterCent,
            long frozenAfterCent,
            String sourceType,
            String sourceNo,
            LocalDateTime occurredAt) {
    }

    record OrganizationPageQuery(
            long tenantId,
            long organizationId,
            Long organizationUserId,
            String entryType,
            LocalDateTime occurredFrom,
            LocalDateTime occurredTo,
            String sourceNo,
            long highWatermark,
            LocalDateTime lastOccurredAt,
            UUID lastOrganizationUserUid,
            Long lastEntrySequenceNo,
            int fetchLimit) {
    }
}
