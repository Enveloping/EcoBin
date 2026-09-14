package org.enveloping.ecobin.device.web.v1;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import jakarta.validation.constraints.AssertTrue;

import java.time.Instant;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public final class DeviceModels {

    private DeviceModels() {
    }

    public record PageData<T>(
            List<T> items,
            int page,
            int pageSize,
            long total) {

        public PageData {
            items = List.copyOf(items);
        }
    }

    public record CursorPage<T>(
            List<T> items,
            Long nextBeforeVersionNo) {

        public CursorPage {
            items = List.copyOf(items);
        }
    }

    public record CreateDeviceAssetRequest(
            @NotBlank @Size(max = 64) String hardwareSn,
            @NotBlank @Size(max = 100) String modelCode,
            @Size(max = 64) String productionBatch,
            @NotNull @Min(1) @Max(6) Integer expectedPortCount) {
    }

    public record ComputedOneNetMapping(
            String productId,
            String deviceName,
            boolean currentComputedValue) {
    }

    /**
     * OneNet 当前连接投影。
     *
     * <p>它来自设备上线/下线事实，不使用运行快照的年龄推断在线状态。</p>
     */
    public record DeviceConnectivityView(
            String oneNetConnectionStatus,
            Instant statusObservedAt,
            Instant statusReceivedAt,
            String evidenceSource) {
    }

    public record DeviceAssetView(
            UUID assetUid,
            String deviceCode,
            String hardwareSn,
            String modelCode,
            String productionBatch,
            int expectedPortCount,
            String tenantCode,
            String organizationCode,
            String acceptanceStatus,
            Boolean mcuRemoteUpdateCapable,
            String deviceEntryUrl,
            String lifecycleStatus,
            long version,
            Instant tenantAssignedAt,
            Instant organizationAssignedAt,
            Instant acceptedAt,
            Instant disabledAt,
            Instant retiredAt,
            Instant createdAt,
            Instant updatedAt,
            DeviceInstallationProfileView installationProfile,
            DeviceConnectivityView connectivity,
            DeviceManagementSummaryView deviceManagement,
            ComputedOneNetMapping oneNetMapping,
            DeviceListStatusView listStatus) {
        public DeviceAssetView withListStatus(DeviceListStatusView status) {
            return new DeviceAssetView(assetUid, deviceCode, hardwareSn, modelCode,
                    productionBatch, expectedPortCount, tenantCode, organizationCode,
                    acceptanceStatus, mcuRemoteUpdateCapable, deviceEntryUrl,
                    lifecycleStatus, version, tenantAssignedAt, organizationAssignedAt,
                    acceptedAt, disabledAt, retiredAt, createdAt, updatedAt,
                    installationProfile, connectivity, deviceManagement, oneNetMapping, status);
        }
    }

    public record DeviceListStatusView(
            List<String> faults, Instant observedAt, List<DeviceListPortView> ports) {
        public DeviceListStatusView {
            faults = List.copyOf(faults);
            ports = List.copyOf(ports);
        }
    }

    public record DeviceListPortView(
            int portNo, String displayName,
            Long reportedWeightGrams, Boolean weightValueAvailable,
            String weightSensorHealth, String weightMeasurementStatus,
            Instant observedAt, Boolean weightFull, Instant fullnessObservedAt,
            String infraredValue, String infraredSensorHealth,
            List<String> faults) {
        public DeviceListPortView { faults = List.copyOf(faults); }
    }

    /**
     * 设备管理程序对“能否开始新物理业务”的只读摘要。
     *
     * <p>尚未迁移的设备始终返回 {@code LEGACY_DIRECT}，此时新架构的准入和
     * 兼容性字段为空，调用方必须继续沿用既有联网、配置、安全和占用检查。</p>
     */
    public record DeviceManagementSummaryView(
            String architectureGeneration,
            String businessAdmission,
            String compatibility,
            DeviceManagementReasonView primaryReason,
            Instant observedAt) {
    }

    public record DeviceManagementReasonView(
            String code,
            String title,
            String description,
            boolean blocksNewBusiness) {
    }

    public record DeviceProtocolVersionView(
            int major,
            int minor) {
    }

    /**
     * 最新可靠设备软件事实及由后台计算出的当前兼容性。
     *
     * <p>这些字段只用于接收、判断和展示；第二阶段不提供业务程序更新下发能力。</p>
     */
    public record DeviceManagementStatusView(
            String architectureGeneration,
            String businessAdmission,
            String compatibility,
            DeviceManagementReasonView primaryReason,
            Instant observedAt,
            List<DeviceManagementReasonView> reasons,
            String deviceGateState,
            Long managementStateSequence,
            String communicationAgentVersion,
            String deviceUpdaterVersion,
            UUID businessReleaseUid,
            String businessVersionName,
            Long businessReleaseSequence,
            String businessPackageSha256,
            String businessProcessState,
            Boolean businessReady,
            String mcuFirmwareVersion,
            String mcuFirmwareIdentityHex,
            DeviceProtocolVersionView managementTransportProtocol,
            DeviceProtocolVersionView deviceMaintenanceProtocol,
            DeviceProtocolVersionView agentBusinessProtocol,
            DeviceProtocolVersionView agentUpdaterProtocol,
            DeviceProtocolVersionView updaterBusinessProtocol,
            DeviceProtocolVersionView uartProtocol,
            UUID sourceEventUid) {

        public DeviceManagementStatusView {
            reasons = List.copyOf(reasons);
        }
    }

    public record DeviceInstallationProfileView(
            String deviceCode,
            long version,
            boolean complete,
            String displayName,
            String address,
            String longitude,
            String latitude,
            String coordinateSystem,
            Instant updatedAt) {
    }

    public record UpdateDeviceInstallationProfileRequest(
            @NotNull @Min(0) @Max(9007199254740991L) Long expectedVersion,
            @NotBlank @Size(max = 100) String displayName,
            @NotBlank @Size(max = 500) String address,
            @NotBlank String longitude,
            @NotBlank String latitude) {
    }

    public record AssignTenantRequest(
            @NotBlank @Size(max = 32) String tenantCode,
            @NotNull @Min(0) Long expectedVersion) {
    }

    public record AssignOrganizationRequest(
            @NotBlank @Size(max = 32) String organizationCode,
            @NotNull @Min(0) Long expectedVersion) {
    }

    public record DeviceControlRequest(
            @NotNull @Min(0) Long expectedVersion,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record BaselineMeasurementAttemptRequest(
            @NotNull UUID expectedLatestMeasurementUid,
            @NotNull @AssertTrue Boolean causeFixedConfirmed,
            @NotNull @AssertTrue Boolean emptyBagConfirmed,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record BaselineMeasurementAcceptedView(
            UUID measurementUid,
            UUID taskUid,
            String state,
            String statusUrl,
            long recommendedPollAfterMs) {
    }

    public record DeliveryNotStartedConfirmationRequest(
            @NotNull UUID expectedTaskUid,
            @NotNull @Min(0) Long expectedSessionVersion,
            @NotNull @AssertTrue Boolean causeFixedConfirmed,
            @NotNull @AssertTrue Boolean deliveryNeverStartedConfirmed,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record DeliveryNotStartedConfirmationView(
            UUID sessionUid,
            UUID taskUid,
            String sessionStatus,
            Instant endedAt,
            String nextAction) {
    }

    /** Closes one unknowable delivery and safety-disables its device. */
    public record AbnormalDeliveryTerminationRequest(
            @NotNull UUID expectedTaskUid,
            @NotNull @Min(0) Long expectedSessionVersion,
            @NotNull @Min(0) Long expectedAssetVersion,
            @NotNull @AssertTrue Boolean physicalOutcomeUnknownConfirmed,
            @NotNull @AssertTrue Boolean devicePoweredOffConfirmed,
            @NotNull @AssertTrue Boolean motionAreaClearConfirmed,
            @NotNull @AssertTrue Boolean deliveryDoorClosedConfirmed,
            @NotNull @AssertTrue Boolean mechanismClearConfirmed,
            @NotBlank @Size(max = 500) String reason) {
    }

    /**
     * 人工发起的“物理结果未知”隔离收口请求。
     *
     * <p>这些确认只授权设备采集新的安全证据并取消原业务，不能把原投递解释为
     * 成功，也不能据此创建订单或资金记录。</p>
     */
    public record DeliveryRecoveryQuarantineRequest(
            @NotNull UUID expectedTaskUid,
            @NotNull @Min(0) Long expectedSessionVersion,
            @NotNull @AssertTrue Boolean physicalOutcomeUnknownConfirmed,
            @NotNull @AssertTrue Boolean causeFixedConfirmed,
            @NotNull @AssertTrue Boolean devicePowerCycledConfirmed,
            @NotNull @AssertTrue Boolean motionAreaClearConfirmed,
            @NotNull @AssertTrue Boolean deliveryDoorClosedConfirmed,
            @NotNull @AssertTrue Boolean mechanismClearConfirmed,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record DeliveryRecoveryQuarantineView(
            UUID recoveryUid,
            UUID sessionUid,
            UUID originalCommandUid,
            UUID commandUid,
            UUID taskUid,
            String state,
            String businessValue,
            String reason,
            Integer portNo,
            Instant requestedAt,
            Instant appliedAt,
            String statusUrl,
            String evidenceSha256,
            Map<String, Object> operatorConfirmations,
            Map<String, Object> deviceEvidence,
            Map<String, Object> existingData) {

        public DeliveryRecoveryQuarantineView {
            operatorConfirmations = Collections.unmodifiableMap(
                    new LinkedHashMap<>(operatorConfirmations));
            deviceEvidence = Collections.unmodifiableMap(
                    new LinkedHashMap<>(deviceEvidence));
            existingData = Collections.unmodifiableMap(
                    new LinkedHashMap<>(existingData));
        }
    }

    public record DeviceTechnicalIssueView(
            String issueUid,
            String category,
            String state,
            String severity,
            String code,
            String title,
            String description,
            Integer portNo,
            UUID taskUid,
            UUID deliverySessionUid,
            Long deliverySessionVersion,
            UUID latestMeasurementUid,
            String blockedReasonCode,
            Integer httpStatus,
            String externalErrorCode,
            String diagnostic,
            Integer automaticAttemptNo,
            Integer automaticAttemptLimit,
            Instant occurredAt,
            List<String> nextActions) {

        public DeviceTechnicalIssueView {
            nextActions = List.copyOf(nextActions);
        }
    }

    public record RuntimeSnapshotPolicyReleaseRequest(
            @NotNull @Min(1) Long expectedVersion,
            @NotNull @Min(10) @Max(71582)
            Integer fallbackIntervalMinutes,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record RuntimeSnapshotPolicyView(
            long version,
            int fallbackIntervalMinutes,
            int minimumIntervalMinutes,
            int maximumIntervalMinutes,
            String publicationSource,
            String updatedBy,
            String changeReason,
            Instant updatedAt,
            UUID rolloutUid,
            String rolloutStatus,
            long targetDeviceCount,
            long processedDeviceCount,
            long publishedDeviceCount,
            long pendingDeviceCount,
            long edgeSavedDeviceCount,
            long appliedDeviceCount,
            long failedDeviceCount,
            long blockedDeviceCount) {
    }

    public record DevicePolicyReleaseRequest(
            @NotNull @Min(0) Long expectedVersion,
            @NotNull @Min(1) Long expectedDefaultVersion,
            @NotBlank String configurationMode,
            String unitPriceYuanPerKg,
            String fullnessMode,
            String fullnessWeightKg,
            Long negativeWeightThresholdGram,
            @NotBlank @Size(max = 500) String reason) { }

    public record DevicePolicyValues(
            String unitPriceYuanPerKg, String fullnessMode, String fullnessWeightKg,
            long negativeWeightThresholdGram) { }

    public record DevicePolicyView(
            long version, long defaultVersion, String configurationMode,
            String unitPriceYuanPerKg, String fullnessMode, String fullnessWeightKg,
            long negativeWeightThresholdGram, DevicePolicyValues platformDefaults,
            String publicationSource, String updatedBy, String changeReason, Instant updatedAt,
            UUID rolloutUid, String rolloutStatus, long targetDeviceCount,
            long processedDeviceCount, long publishedDeviceCount, long pendingDeviceCount,
            long edgeSavedDeviceCount, long appliedDeviceCount, long failedDeviceCount, long blockedDeviceCount) { }

    public record AcceptanceEvidenceView(
            UUID evidenceUid,
            int schemaVersion,
            String edgeSoftwareVersion,
            String edgeProtocolVersion,
            boolean oneNetOnline,
            boolean persistentStoreHealthy,
            boolean trustedTimeHealthy,
            boolean configurationPersistenceHealthy,
            boolean mcuCommunicationHealthy,
            Boolean mcuRemoteUpdateCapable,
            boolean sensorsHealthy,
            boolean camerasCaptureHealthy,
            boolean cameraUploadHealthy,
            Boolean deviceEntryUrlStored,
            String deviceEntryUrlSha256,
            Boolean deviceEntryUrlMcuApplied,
            String deviceEntryUrlAppliedSha256,
            Long deviceEntryUrlAppliedMcuBootId,
            String deviceEntryUrlDisplayBasis,
            boolean mcuSimulated,
            boolean camerasSimulated,
            String evaluationStatus,
            List<String> failureReasons,
            String evidenceSha256,
            Instant observedAt,
            Instant receivedAt) {

        public AcceptanceEvidenceView {
            failureReasons = List.copyOf(failureReasons);
        }
    }

    public record FactoryProgressView(
            FactoryBagProgressView factoryBags,
            FactoryAcceptanceProgressView acceptance,
            ReliableTaskProgressView acceptanceRequest,
            FactorySealProgressView seal,
            String currentStage,
            String status,
            String blockingCode,
            List<String> nextActionCodes,
            Instant fetchedAt) {

        public FactoryProgressView {
            nextActionCodes = List.copyOf(nextActionCodes);
        }
    }

    public record FactoryBagProgressView(
            int expectedPortCount,
            int verifiedCount,
            boolean complete,
            long revision) {
    }

    public record FactoryAcceptanceProgressView(
            String status,
            long generation,
            List<String> currentFailureReasons,
            Instant lastEvaluatedAt,
            Instant acceptedAt,
            AcceptanceEvidenceSummary authoritativeEvidence,
            AcceptanceEvidenceSummary latestEvidence) {

        public FactoryAcceptanceProgressView {
            currentFailureReasons = List.copyOf(currentFailureReasons);
        }
    }

    public record AcceptanceEvidenceSummary(
            UUID evidenceUid,
            String evaluationStatus,
            String evidenceSha256,
            Instant receivedAt) {
    }

    public record ReliableTaskProgressView(
            UUID taskUid,
            String taskState,
            String blockedReasonCode,
            String blockedDiagnostic,
            ReliableTaskAttemptSummary latestAttempt) {
    }

    public record ReliableTaskAttemptSummary(
            Long attemptNo,
            String technicalResult,
            Integer httpStatus,
            String externalErrorCode,
            String externalRequestId,
            String diagnostic,
            Instant recordedAt) {
    }

    public record FactorySealProgressView(
            String status,
            long generation,
            String cancellationReason,
            UUID taskUid,
            String taskState,
            String blockedReasonCode,
            String blockedDiagnostic,
            ReliableTaskAttemptSummary latestAttempt,
            Instant acknowledgedAt,
            Instant sealedAt,
            Instant cleanupCompletedAt,
            Instant completionReceivedAt) {
    }

    public record PortView(
            int portNo,
            String displayName,
            Boolean enabled,
            String unitPriceYuanPerKg,
            String fullnessMode,
            Long configurationVersion) {
    }

    public record RuntimeConfigurationSummary(
            Long latestPublishedVersion,
            Long latestAppliedVersion,
            String latestApplicationStatus,
            boolean latestPreciselyApplied) {
    }

    public record RuntimeHealthSummary(
            String edgeConnectionStatus,
            String oneNetConnectionStatus,
            Instant oneNetStatusObservedAt,
            Instant oneNetStatusReceivedAt,
            String oneNetEvidenceSource,
            Instant trustedRuntimeReceivedAt,
            String mcuLinkStatus,
            String safetyStatus,
            String aggregateWeightHealth,
            String cameraHealth,
            String localStorageHealth,
            String clockSyncHealth,
            String edgeSoftwareVersion,
            String mcuFirmwareVersion,
            String uartState,
            Integer uartProtocolMajor,
            Integer uartProtocolMinor,
            String capabilityBitmapHex,
            Long edgeBootId,
            String lastMcuResetReason,
            Long pendingReliableEventCount,
            Long orangePiReportedConfigurationVersion,
            Instant lastHeartbeatAt,
            Instant lastDeviceEventAt,
            Long runtimeVersion) {
    }

    public record DeviceRuntimeView(
            String deviceCode,
            String lifecycleStatus,
            String acceptanceStatus,
            long version,
            RuntimeConfigurationSummary configuration,
            RuntimeHealthSummary health,
            DeviceManagementStatusView deviceManagement,
            boolean occupied,
            String occupancyKind,
            Instant occupiedAt,
            List<PortRuntimeView> ports,
            Instant fetchedAt) {

        public DeviceRuntimeView {
            ports = List.copyOf(ports);
        }
    }

    public record PortRuntimeView(
            String deviceCode,
            int portNo,
            String displayName,
            Boolean configuredEnabled,
            String deliveryDoorState,
            String deliveryDoorActuatorHealth,
            String deliveryDoorContactState,
            String lastDeliveryDoorCommand,
            String lastDeliveryDoorOutputStatus,
            String cleanLockPowerState,
            String cleanSolenoidHealth,
            String cleanDoorRecordedState,
            String cleanDoorStateBasis,
            Boolean cleanerPhysicalCloseConfirmed,
            String weightSensorHealth,
            String weightMeasurementStatus,
            Boolean weightValueAvailable,
            Long reportedWeightGrams,
            String weightValueKind,
            String infraredValue,
            String infraredSensorHealth,
            String fullnessSensorKind,
            String fullnessSensorValue,
            Long representativeDistanceMm,
            String smokeState,
            String smokeSensorHealth,
            String safetyStatus,
            Instant lastObservedAt,
            Long runtimeVersion) {
    }

    public record ConfigurationReleaseRequest(
            @NotNull @Min(0) Long expectedLatestVersion,
            @Size(max = 500) String reason,
            @NotNull @Valid ConfigurationDeviceRequest device,
            @NotEmpty @Size(max = 6)
            List<@Valid ConfigurationPortRequest> ports) {

        public ConfigurationReleaseRequest {
            ports = ports == null ? null : List.copyOf(ports);
        }
    }

    public record ConfigurationResynchronizationRequest(
            @NotNull @Min(0) Long expectedVersion,
            @NotBlank @Size(max = 500) String reason) {
    }

    /**
     * 平台恢复配置版本序列时，只确认当前最高版本并说明原因。
     *
     * <p>服务端复制当前完整快照并生成更高版本，避免平台管理员手工重填机构价格、
     * 传感器阈值等机构配置。</p>
     */
    public record ConfigurationRollForwardRequest(
            @NotNull @Min(1) Long expectedLatestVersion,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record ConfigurationDeviceRequest(
            @NotNull @Min(1) Long mcuHeartbeatIntervalMs,
            @NotNull @Min(1) Long mcuHeartbeatMissThreshold,
            @NotNull @Min(0) Long doorCloseRetryLimit,
            @NotNull @Min(1) Long continueDeliveryWaitMs,
            @NotNull @Min(1) Long negativeWeightThresholdGram,
            Long deliveryAutoCloseMs,
            Long weightMeasurementTimeoutMs,
            Long deliveryDoorTravelWaitMs,
            Long cleanSolenoidPulseMs,
            Boolean smokeMonitoringEnabled) {
    }

    public record ConfigurationPortRequest(
            @NotNull @Min(1) @Max(6) Integer portNo,
            @NotBlank @Size(max = 32) String displayName,
            @NotNull Boolean enabled,
            @NotBlank String unitPriceYuanPerKg,
            @NotBlank String fullnessMode,
            @NotBlank String fullnessWeightKg,
            @NotNull @Min(0) Long deliverySettleDelayMs,
            @NotNull @Min(0) Long fullnessInitialDelayMs,
            @NotNull @Min(0) Long fullnessRecheckDelayMs,
            @NotNull @Min(1000) Long doorAutoCloseTimeoutMs,
            String fullnessSensorKind,
            Long fullnessDistanceThresholdMm,
            Integer fullnessSampleCount,
            Integer fullnessMinimumValidSampleCount,
            Long fullnessEchoTimeoutUs,
            Long weightStableWindowMs,
            Long weightMaximumFluctuationGram,
            Integer weightRequiredSampleCount,
            Long weightMeasurementTimeoutMs,
            Long weightMinimumGram,
            Long weightMaximumGram,
            Long calibrationVersion,
            Long infraredSampleTimeoutMs,
            Long deliveryDoorOperationTimeoutMs) {
    }

    public record ConfigurationDeviceSnapshot(
            long edgeHeartbeatIntervalMs,
            long edgeHeartbeatMissThreshold,
            long mcuHeartbeatIntervalMs,
            long mcuHeartbeatMissThreshold,
            long doorCloseRetryLimit,
            long continueDeliveryWaitMs,
            long negativeWeightThresholdGram,
            long deliveryAutoCloseMs,
            long weightMeasurementTimeoutMs,
            long deliveryDoorTravelWaitMs,
            long cleanSolenoidPulseMs,
            boolean smokeMonitoringEnabled) {
    }

    public record ConfigurationPortSnapshot(
            int portNo,
            String displayName,
            boolean enabled,
            String unitPriceYuanPerKg,
            String fullnessMode,
            String fullnessWeightKg,
            long deliverySettleDelayMs,
            long fullnessInitialDelayMs,
            long fullnessRecheckDelayMs,
            long doorAutoCloseTimeoutMs,
            String fullnessSensorKind,
            long fullnessDistanceThresholdMm,
            int fullnessSampleCount,
            int fullnessMinimumValidSampleCount,
            long fullnessEchoTimeoutUs,
            long weightStableWindowMs,
            long weightMaximumFluctuationGram,
            int weightRequiredSampleCount,
            long weightMeasurementTimeoutMs,
            long weightMinimumGram,
            long weightMaximumGram,
            long calibrationVersion,
            long infraredSampleTimeoutMs,
            long deliveryDoorOperationTimeoutMs) {
    }

    public record ConfigurationApplicationSummary(
            UUID applicationUid,
            String status,
            String dispatchState,
            long version) {
    }

    public record ConfigurationVersionSummary(
            long versionNo,
            Long runtimeSnapshotPolicyVersion,
            String contentSha256,
            String mcuPayloadSha256,
            String publicationSource,
            String publishedBy,
            Instant publishedAt,
            ConfigurationApplicationSummary application) {
    }

    public record ConfigurationVersionView(
            String deviceCode,
            long versionNo,
            int schemaVersion,
            Long runtimeSnapshotPolicyVersion,
            String contentSha256,
            String mcuPayloadSha256,
            ConfigurationDeviceSnapshot device,
            List<ConfigurationPortSnapshot> ports,
            String publicationSource,
            String publishedBy,
            Instant publishedAt,
            ConfigurationApplicationSummary application) {

        public ConfigurationVersionView {
            ports = List.copyOf(ports);
        }
    }

    public record ConfigurationAcceptedView(
            UUID operationId,
            UUID resourceId,
            UUID applicationUid,
            long versionNo,
            String contentSha256,
            String mcuPayloadSha256,
            String status,
            String dispatchState,
            String statusUrl,
            Long recommendedPollAfterMs) {
    }

    public record ConfigurationApplicationView(
            UUID applicationUid,
            long versionNo,
            String contentSha256,
            String mcuPayloadSha256,
            String status,
            long version,
            boolean latestDesired,
            boolean superseded,
            Long deviceReportedVersionNo,
            String deviceReportedContentSha256,
            String deviceReportedMcuPayloadSha256,
            Instant edgePersistedAt,
            Instant mcuSyncedAt,
            Instant appliedAt,
            String lastFailureCode,
            Instant lastFailedAt,
            String dispatchState,
            Long recommendedPollAfterMs,
            List<String> nextActions) {

        public ConfigurationApplicationView {
            nextActions = List.copyOf(nextActions);
        }
    }
}
