package org.enveloping.ecobin.device.application.startdelivery;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

interface StartDeliveryDeviceRepository {

    LocalDateTime databaseNow();

    List<Long> lockActiveSessionIds(
            long tenantId,
            long organizationId,
            long organizationUserId);

    Optional<AssetRow> lockAssetByDeviceCode(String deviceCode);

    Optional<SubjectStatusRow> lockTenant(long tenantId);

    Optional<SubjectStatusRow> lockOrganization(
            long tenantId,
            long organizationId);

    Optional<TransportPresenceRow> lockTransportPresence(long assetId);

    Optional<OccupancyRow> lockOccupancy(long assetId);

    List<Long> lockReleasedPendingDeliveryIds(long assetId);

    Optional<ConfigurationRow> lockLatestConfiguration(
            long tenantId,
            long organizationId,
            long assetId);

    Optional<PortRow> lockPort(
            long tenantId,
            long organizationId,
            long assetId,
            int portNo);

    Optional<PortConfigurationRow> lockPortConfiguration(
            long tenantId,
            long organizationId,
            long assetId,
            long configurationId,
            long portId);

    long insertSession(SessionInsert insert);

    void insertOccupancy(
            long assetId,
            long tenantId,
            long organizationId,
            long sessionId,
            LocalDateTime acquiredAt);

    long insertCommand(CommandInsert insert);

    record AssetRow(
            long id,
            String hardwareSn,
            String devicePublicCode,
            String lifecycleStatus,
            String acceptanceStatus,
            Long tenantId,
            Long organizationId,
            int expectedPortCount,
            String managementArchitectureGeneration,
            String businessAdmissionStatus) {
    }

    record SubjectStatusRow(long id, String status) {
    }

    record TransportPresenceRow(String onenetConnectionStatus) {
    }

    record OccupancyRow(String occupancyKind) {
    }

    record ConfigurationRow(
            long id,
            long version,
            byte[] contentSha256,
            byte[] mcuPayloadSha256,
            long edgeHeartbeatIntervalMs,
            long edgeHeartbeatMissThreshold,
            long continueDeliveryWaitMs,
            long negativeWeightThresholdGrams,
            long deliveryAutoCloseMs,
            boolean applicationApplied,
            boolean runtimeApplied) {
    }

    record PortRow(long id, int portNo) {
    }

    record PortConfigurationRow(
            long id,
            boolean businessEnabled,
            BigDecimal unitPriceYuanPerKg,
            String fullnessMode,
            long calibrationVersion) {
    }

    record SessionInsert(
            UUID sessionUid,
            long tenantId,
            long organizationId,
            long assetId,
            long portId,
            long organizationUserId,
            long deviceConfigurationId,
            long deviceConfigurationVersion,
            byte[] deviceConfigurationContentSha256,
            byte[] deviceConfigurationMcuPayloadSha256,
            long portConfigurationId,
            long deliveryConfigurationId,
            byte[] deliveryConfigurationContentSha256,
            long bagId,
            UUID bagUid,
            String bagCode,
            BigDecimal unitPriceYuanPerKg,
            long openBalanceFloorCent,
            long maxReviewAbsWeightGrams,
            long negativeWeightThresholdGrams,
            long continueDeliveryWaitMs,
            LocalDateTime authorizationExpiresAt,
            LocalDateTime resultRecoveryDeadlineAt,
            LocalDateTime now) {
    }

    record CommandInsert(
            UUID commandUid,
            long tenantId,
            long organizationId,
            long assetId,
            long deliverySessionId,
            String semanticEnvelopeJson,
            byte[] semanticEnvelopeSha256,
            LocalDateTime now) {
    }
}
