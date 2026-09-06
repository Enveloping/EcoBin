package org.enveloping.ecobin.device.web.v1.software;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class BusinessReleaseModels {

    private BusinessReleaseModels() {
    }

    public record CreateReleaseDraftRequest(
            @NotBlank
            @Pattern(regexp = "^(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)\\."
                    + "(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+"
                    + "(?:\\.[0-9A-Za-z-]+)*)?(?:\\+[0-9A-Za-z-]+"
                    + "(?:\\.[0-9A-Za-z-]+)*)?$")
            @Size(max = 32)
            String versionName,
            @Size(max = 1000) String releaseNotes) {
    }

    public record ReleaseActionRequest(
            @NotBlank @Size(max = 500) String reason) {
    }

    public record CreateRolloutRequest(
            @NotNull UUID releaseUid,
            @NotBlank @Size(max = 64) String validationHardwareSn,
            @Size(max = 1000) List<
                    @NotBlank @Size(max = 64) String> targetHardwareSns,
            @Min(1) @Max(100) Integer batchSize,
            @NotBlank @Size(max = 500) String reason) {

        public CreateRolloutRequest {
            targetHardwareSns = targetHardwareSns == null
                    ? List.of() : List.copyOf(targetHardwareSns);
        }
    }

    public record ControlPlaneReadinessView(
            boolean artifactStorageAvailable,
            String artifactStorageMessage,
            boolean signingKeysAvailable,
            String signingKeysMessage,
            boolean remoteDispatchEnabled,
            String dispatchMessage) {
    }

    public record CompatibilityDeclarationView(
            int packageFormatVersion,
            int backendCommandContractVersion,
            int deviceEventContractVersion,
            String communicationBusinessProtocol,
            String updaterBusinessProtocol,
            String uartProtocol,
            String requiredMcuCapabilities,
            String providedBusinessCapabilities) {
    }

    public record ReleaseActionView(
            String action,
            String actionLabel,
            String resultingStatus,
            String resultingStatusLabel,
            String requestedBy,
            String reason,
            Instant createdAt) {
    }

    public record ReleaseView(
            UUID releaseUid,
            String versionName,
            long releaseSequence,
            String status,
            String statusLabel,
            String statusDescription,
            String packageObjectKey,
            String signatureObjectKey,
            String packageSha256,
            Long packageSize,
            String signatureSha256,
            String signingKeyId,
            boolean artifactUploaded,
            String verificationErrorMessage,
            String releaseNotes,
            String createdBy,
            String verifiedBy,
            Instant verifiedAt,
            String approvedBy,
            Instant approvedAt,
            String suspendedBy,
            Instant suspendedAt,
            String suspensionReason,
            String retiredBy,
            Instant retiredAt,
            String retirementReason,
            Instant createdAt,
            Instant updatedAt,
            CompatibilityDeclarationView compatibility,
            List<ReleaseActionView> actions) {

        public ReleaseView {
            actions = List.copyOf(actions);
        }
    }

    public record DeploymentView(
            UUID deploymentUid,
            String hardwareSn,
            String tenantCode,
            String organizationCode,
            String kind,
            String kindLabel,
            int waveNo,
            String status,
            String statusLabel,
            String cancellationStatus,
            String cancellationStatusLabel,
            String cancelReason,
            Instant cancelRequestedAt,
            Instant cancelResultAt,
            String businessAdmissionLabel,
            int downloadAttemptCount,
            int targetAttemptCount,
            int rollbackAttemptCount,
            String installedVersionName,
            boolean databaseRestored,
            String errorMessage,
            String eligibilitySummary,
            long sourceManagementStateSequence,
            UUID currentBusinessReleaseUid,
            Long currentBusinessReleaseSequence,
            Instant plannedAt,
            Instant queuedAt,
            Instant completedAt,
            Instant updatedAt) {
    }

    public record RolloutActionView(
            String action,
            String actionLabel,
            String resultingStatus,
            String resultingStatusLabel,
            String requestedBy,
            String reason,
            Instant createdAt) {
    }

    public record RolloutView(
            UUID rolloutUid,
            ReleaseView release,
            String status,
            String statusLabel,
            int batchSize,
            int maximumWaveNo,
            String validationHardwareSn,
            int observationWindowMinutes,
            int downloadTimeoutMinutes,
            int drainTimeoutMinutes,
            int maximumRetryCount,
            boolean remoteDispatchEnabled,
            String reason,
            String createdBy,
            String stoppedBy,
            Instant stoppedAt,
            String stopReason,
            Instant createdAt,
            Instant updatedAt,
            List<DeploymentView> deployments,
            List<RolloutActionView> actions) {

        public RolloutView {
            deployments = List.copyOf(deployments);
            actions = List.copyOf(actions);
        }
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
}
