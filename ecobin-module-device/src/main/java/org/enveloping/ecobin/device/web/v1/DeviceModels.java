package org.enveloping.ecobin.device.web.v1;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
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
            @NotNull @Min(1) @Max(6) Integer expectedPortCount,
            @NotEmpty @Size(max = 6)
            List<@Valid FactoryInstalledBagRequest> factoryBags) {

        public CreateDeviceAssetRequest {
            factoryBags = factoryBags == null
                    ? null : List.copyOf(factoryBags);
        }
    }

    public record FactoryInstalledBagRequest(
            @NotNull @Min(1) @Max(6) Integer portNo,
            @NotBlank @Size(min = 8, max = 64) String bagCode) {
    }

    public record ComputedOneNetMapping(
            String productId,
            String deviceName,
            boolean currentComputedValue) {
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
            ComputedOneNetMapping oneNetMapping) {
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
            boolean sensorsHealthy,
            boolean camerasCaptureHealthy,
            boolean cameraUploadHealthy,
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
            Instant lastHeartbeatAt,
            Instant lastDeviceEventAt,
            long runtimeVersion) {
    }

    public record DeviceRuntimeView(
            String deviceCode,
            String lifecycleStatus,
            String acceptanceStatus,
            long version,
            RuntimeConfigurationSummary configuration,
            RuntimeHealthSummary health,
            boolean occupied,
            boolean deliveryAllowed,
            boolean cleaningAllowed,
            List<String> deliveryBlockers,
            List<String> cleaningBlockers) {

        public DeviceRuntimeView {
            deliveryBlockers = List.copyOf(deliveryBlockers);
            cleaningBlockers = List.copyOf(cleaningBlockers);
        }
    }

    public record PortBusinessSummary(
            boolean currentBagPresent,
            String baselineState,
            String detectionGate,
            String fullnessState,
            String displayedFullnessPercent,
            boolean cleanOperationActive) {
    }

    public record PortRuntimeView(
            String deviceCode,
            int portNo,
            String deliveryDoorState,
            String deliveryDoorActuatorHealth,
            String deliveryDoorContactState,
            String cleanLockPowerState,
            String cleanSolenoidHealth,
            String cleanDoorPhysicalState,
            String cleanDoorStateBasis,
            String weightSensorHealth,
            String infraredValue,
            String infraredSensorHealth,
            String smokeState,
            String smokeSensorHealth,
            String safetyStatus,
            PortBusinessSummary business,
            boolean deliveryAllowed,
            boolean cleaningAllowed,
            List<String> deliveryBlockers,
            List<String> cleaningBlockers,
            Instant lastObservedAt,
            long runtimeVersion) {

        public PortRuntimeView {
            deliveryBlockers = List.copyOf(deliveryBlockers);
            cleaningBlockers = List.copyOf(cleaningBlockers);
        }
    }

    public record ConfigurationReleaseRequest(
            @NotNull @Min(0) Long expectedLatestVersion,
            @Size(max = 500) String reason,
            @NotNull Boolean locationCorrectionConfirmed,
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

    public record ConfigurationDeviceRequest(
            @NotBlank @Size(max = 100) String displayName,
            @Size(max = 500) String address,
            String longitude,
            String latitude,
            @NotNull @Min(1) Long edgeHeartbeatIntervalMs,
            @NotNull @Min(1) Long edgeHeartbeatMissThreshold,
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
            String displayName,
            String address,
            String longitude,
            String latitude,
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
            String contentSha256,
            String mcuPayloadSha256,
            String deviceDisplayName,
            String publicationSource,
            String publishedBy,
            Instant publishedAt,
            ConfigurationApplicationSummary application) {
    }

    public record ConfigurationVersionView(
            String deviceCode,
            long versionNo,
            int schemaVersion,
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
