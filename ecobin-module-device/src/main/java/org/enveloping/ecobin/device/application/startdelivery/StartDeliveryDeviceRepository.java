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

    Optional<Long> findAssetIdByDeploymentCode(String deploymentCode);

    Optional<AssetRow> lockAsset(long assetId);

    Optional<ActiveDeploymentRow> lockActiveDeployment(long assetId);

    Optional<DeploymentRow> lockDeployment(long deploymentId);

    Optional<TransportPresenceRow> lockTransportPresence(long assetId);

    Optional<OccupancyRow> lockOccupancy(long assetId);

    Optional<ConfigurationRow> lockLatestConfiguration(
            long tenantId,
            long organizationId,
            long deploymentId);

    Optional<PortRow> lockPort(
            long tenantId,
            long organizationId,
            long deploymentId,
            int portNo);

    Optional<PortConfigurationRow> lockPortConfiguration(
            long tenantId,
            long organizationId,
            long deploymentId,
            long configurationId,
            long portId);

    long insertSession(SessionInsert insert);

    void insertOccupancy(
            long assetId,
            long tenantId,
            long organizationId,
            long deploymentId,
            long sessionId,
            LocalDateTime acquiredAt);

    long insertCommand(CommandInsert insert);

    record AssetRow(
            long id,
            String hardwareSn,
            String lifecycleStatus,
            int expectedPortCount) {
    }

    record ActiveDeploymentRow(
            long assetId,
            long tenantId,
            long organizationId,
            long deploymentId) {
    }

    record DeploymentRow(
            long id,
            long tenantId,
            long organizationId,
            long assetId,
            String publicCode,
            String lifecycleStatus,
            boolean businessEnabled) {
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
            long deliveryAutoCloseMs) {
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
            long deploymentId,
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
            long deploymentId,
            long deliverySessionId,
            String semanticEnvelopeJson,
            byte[] semanticEnvelopeSha256,
            LocalDateTime now) {
    }
}
