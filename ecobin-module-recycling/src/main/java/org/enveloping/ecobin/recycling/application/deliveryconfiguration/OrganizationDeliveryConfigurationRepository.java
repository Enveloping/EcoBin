package org.enveloping.ecobin.recycling.application.deliveryconfiguration;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

interface OrganizationDeliveryConfigurationRepository {

    Optional<DeliveryConfigurationRow> findCurrent(
            DeliveryConfigurationScope scope);

    Optional<DeliveryConfigurationRow> lockCurrent(
            DeliveryConfigurationScope scope);

    Optional<DeliveryConfigurationRow> findVersion(
            DeliveryConfigurationScope scope,
            long versionNo);

    List<DeliveryConfigurationRow> findVersions(
            DeliveryConfigurationScope scope,
            Long beforeVersionNo,
            int limit);

    long insertVersion(
            DeliveryConfigurationScope scope,
            NewDeliveryConfigurationVersion version);

    void switchCurrent(
            DeliveryConfigurationScope scope,
            DeliveryConfigurationRow previous,
            long configurationId,
            long versionNo,
            LocalDateTime switchedAt);
}

record DeliveryConfigurationScope(
        long tenantId,
        long organizationId) {
}

record DeliveryConfigurationRow(
        long id,
        long versionNo,
        byte[] contentSha256,
        String reviewMode,
        long openBalanceFloorCent,
        long maxReviewAbsoluteWeightGram,
        String publicationSource,
        UUID publishedByStaffAccountUid,
        String publishedBy,
        LocalDateTime publishedAt,
        boolean current,
        long headLockVersion) {

    DeliveryConfigurationRow {
        contentSha256 = contentSha256.clone();
    }

    @Override
    public byte[] contentSha256() {
        return contentSha256.clone();
    }
}

record NewDeliveryConfigurationVersion(
        long versionNo,
        byte[] contentSha256,
        String reviewMode,
        long openBalanceFloorCent,
        long maxReviewAbsoluteWeightGram,
        String publicationSource,
        Long publishedByStaffAccountId,
        LocalDateTime publishedAt) {

    NewDeliveryConfigurationVersion {
        contentSha256 = contentSha256.clone();
    }

    @Override
    public byte[] contentSha256() {
        return contentSha256.clone();
    }
}
