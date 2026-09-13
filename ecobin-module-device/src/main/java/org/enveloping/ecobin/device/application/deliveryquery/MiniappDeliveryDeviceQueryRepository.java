package org.enveloping.ecobin.device.application.deliveryquery;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

interface MiniappDeliveryDeviceQueryRepository {

    LocalDateTime databaseNow();

    Optional<AssetSnapshotRow> findAsset(
            long tenantId,
            long organizationId,
            String deviceCode);

    List<PortSnapshotRow> findPorts(
            long tenantId,
            long organizationId,
            long assetId,
            Long configurationId);

    Optional<OwnedSessionRow> findOwnedSession(
            long tenantId,
            long organizationId,
            long organizationUserId,
            UUID sessionUid);

    record AssetSnapshotRow(
            long assetId,
            String deviceCode,
            String acceptanceStatus,
            String lifecycleStatus,
            boolean deviceBusy,
            Long configurationId,
            Long configurationVersion,
            String deviceDisplayName,
            String locationAddress,
            Long edgeHeartbeatIntervalMs,
            Long edgeHeartbeatMissThreshold,
            byte[] configurationContentSha256,
            byte[] configurationMcuPayloadSha256,
            String configurationApplicationStatus,
            Long applicationReportedVersion,
            byte[] applicationReportedContentSha256,
            byte[] applicationReportedMcuPayloadSha256,
            LocalDateTime configurationAppliedAt,
            String edgeConnectionStatus,
            String safetyStatus,
            String localStorageHealth,
            String localStorageState,
            Long trustedRuntimeEdgeEventId,
            String trustedRuntimeEdgeEventType,
            Long trustedRuntimeSequence,
            LocalDateTime trustedRuntimeReceivedAt,
            Long progressAppliedConfigurationVersion,
            byte[] progressAppliedConfigurationContentSha256,
            byte[] progressAppliedConfigurationMcuPayloadSha256,
            String managementArchitectureGeneration,
            String businessAdmissionStatus) {
    }

    record PortSnapshotRow(
            long portId,
            int portNo,
            String displayName,
            Boolean businessEnabled,
            BigDecimal unitPriceYuanPerKg,
            String fullnessMode,
            Long configuredCalibrationVersion,
            String deliveryDoorActuatorHealth,
            String cleanLockPowerState,
            String cleanSolenoidHealth,
            String weightSensorHealth,
            String weightMeasurementStatus,
            Boolean weightValueAvailable,
            Long reportedWeightGrams,
            String weightValueKind,
            Long runtimeCalibrationVersion,
            String smokeState,
            String smokeSensorHealth,
            Long runtimeFaultBitmap,
            String safetyStatus,
            Long pendingDeliveryResultSessionId,
            Long trustedRuntimeEdgeEventId,
            String trustedRuntimeEdgeEventType,
            Long trustedRuntimeSequence) {
    }

    record OwnedSessionRow(
            long sessionId,
            UUID sessionUid,
            String deviceStatus,
            String deviceCode,
            int portNo,
            LocalDateTime firstPhysicalProgressAt,
            LocalDateTime deviceCompletedAt,
            LocalDateTime endedAt,
            String endReason,
            LocalDateTime offlineOccupancyReleasedAt) {
    }
}
