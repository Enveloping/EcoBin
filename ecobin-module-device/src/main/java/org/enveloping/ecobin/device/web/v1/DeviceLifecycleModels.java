package org.enveloping.ecobin.device.web.v1;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class DeviceLifecycleModels {

    private DeviceLifecycleModels() {
    }

    public record CreateTenantAllocationRequest(
            @NotBlank @Size(max = 64) String hardwareSn,
            @NotNull @Min(0) Long expectedAssetVersion,
            @Size(max = 500) String reason) {
    }

    public record TenantAllocationView(
            UUID allocationUid,
            String tenantCode,
            String hardwareSn,
            String modelCode,
            int expectedPortCount,
            String allocationStatus,
            String assetLifecycleStatus,
            String allocationSource,
            String currentDeploymentCode,
            String currentOrganizationCode,
            boolean credentialRotationRequired,
            long allocationVersion,
            long assetVersion,
            Instant allocatedAt,
            Instant endedAt,
            String endMode,
            String endReason) {
    }

    public record CreateAllocatedDeploymentRequest(
            @NotNull UUID allocationUid,
            @NotNull @Min(0) Long expectedAllocationVersion) {
    }

    public record ReturnDeploymentToTenantPoolRequest(
            @NotNull @Min(0) Long expectedDeploymentVersion,
            @NotNull @Min(0) Long expectedAllocationVersion,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record ReclaimTenantAllocationRequest(
            @NotNull @Min(0) Long expectedAllocationVersion,
            @NotNull @Min(0) Long expectedAssetVersion,
            @NotBlank String mode,
            @NotNull Boolean physicalPossessionConfirmed,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record CredentialRotationConfirmationRequest(
            @NotNull @Min(0) Long expectedAssetVersion,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record CredentialRotationConfirmationView(
            UUID confirmationUid,
            String hardwareSn,
            Instant confirmedAt,
            String reason) {
    }

    public record MaintenanceClearanceRequest(
            @NotNull @Min(0) Long expectedAssetVersion,
            @NotNull Boolean physicalPossessionConfirmed,
            @NotNull Boolean inspectionConfirmed,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record AcceptanceRequest(
            @NotNull @Min(0) Long expectedDeploymentVersion,
            @NotNull @Min(1) Long expectedConfigurationVersion,
            @NotNull Boolean deliveryDoorObservedNormal,
            @NotNull Boolean camerasObservedNormal,
            @NotNull Boolean cleanDoorInstallationObservedNormal,
            @Size(max = 500) String reason) {
    }

    public record ConfigurationAcceptanceEvidence(
            Long latestVersion,
            Long appliedVersion,
            String applicationStatus,
            boolean preciselyApplied) {
    }

    public record RuntimeAcceptanceEvidence(
            String edgeConnectionStatus,
            String mcuLinkStatus,
            String uartState,
            String aggregateWeightHealth,
            String cameraHealth,
            String localStorageHealth,
            String clockSyncHealth,
            String edgeSoftwareVersion,
            String mcuFirmwareVersion,
            Long pendingReliableEventCount,
            Instant receivedAt) {
    }

    public record PortAcceptanceEvidence(
            int portNo,
            String cleanLockPowerState,
            String cleanSolenoidHealth,
            String weightSensorHealth,
            String infraredValue,
            String infraredSensorHealth,
            String fullnessSensorKind,
            String smokeState,
            String smokeSensorHealth,
            String safetyStatus,
            Long runtimeFaultBitmap,
            Long runtimeEdgeEventId,
            Instant observedAt) {
    }

    public record AcceptanceReadinessView(
            String deploymentCode,
            String readinessMode,
            boolean ready,
            List<String> blockers,
            ConfigurationAcceptanceEvidence configuration,
            RuntimeAcceptanceEvidence runtime,
            List<PortAcceptanceEvidence> ports) {

        public AcceptanceReadinessView {
            blockers = List.copyOf(blockers);
            ports = List.copyOf(ports);
        }
    }

    public record AcceptanceView(
            UUID acceptanceUid,
            String deploymentCode,
            long configurationVersion,
            Instant runtimeReceivedAt,
            boolean deliveryDoorObservedNormal,
            boolean camerasObservedNormal,
            boolean cleanDoorInstallationObservedNormal,
            String acceptedBy,
            String reason,
            Instant acceptedAt) {
    }
}
