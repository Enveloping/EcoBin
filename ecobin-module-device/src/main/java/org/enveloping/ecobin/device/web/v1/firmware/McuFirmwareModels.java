package org.enveloping.ecobin.device.web.v1.firmware;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class McuFirmwareModels {

    private McuFirmwareModels() {
    }

    public record RegisterReleaseRequest(
            @NotNull UUID releaseUid,
            @NotBlank
            @Pattern(regexp =
                    "^[0-9]+\\.[0-9]+\\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
            @Size(max = 32)
            String firmwareVersion,
            @NotNull @Min(1) @Max(4294967295L)
            Long firmwareVersionCode,
            @NotBlank @Pattern(regexp = "^[0-9a-f]{16}$")
            String firmwareIdentityHex,
            @NotBlank @Size(max = 64)
            String hardwareCompatibility,
            @NotNull @Min(2) @Max(2)
            Integer fixedFrameRevision,
            @NotBlank @Size(max = 512)
            String objectKey,
            @NotBlank @Pattern(regexp = "^[0-9a-f]{64}$")
            String packageSha256,
            @NotNull @Min(1) @Max(131072)
            Long packageSize,
            @Size(max = 1000)
            String releaseNotes) {
    }

    public record CreateRolloutRequest(
            @NotNull UUID releaseUid,
            @NotBlank @Size(max = 64) String validationHardwareSn,
            @NotEmpty @Size(max = 1000) List<
                    @NotBlank @Size(max = 64) String> targetHardwareSns,
            @NotNull @Min(1) @Max(100) Integer batchSize,
            @NotBlank @Size(max = 500) String reason) {

        public CreateRolloutRequest {
            targetHardwareSns = targetHardwareSns == null
                    ? null
                    : List.copyOf(targetHardwareSns);
        }
    }

    public record RolloutActionRequest(
            @NotBlank @Size(max = 500) String reason) {
    }

    public record ReleaseView(
            UUID releaseUid,
            String firmwareVersion,
            long firmwareVersionCode,
            String firmwareIdentityHex,
            String hardwareCompatibility,
            int fixedFrameRevision,
            String objectKey,
            String packageSha256,
            long packageSize,
            String status,
            String releaseNotes,
            String createdBy,
            String promotedBy,
            Instant promotedAt,
            Instant createdAt) {
    }

    public record DeploymentView(
            UUID deploymentUid,
            String hardwareSn,
            String tenantCode,
            String organizationCode,
            String kind,
            int waveNo,
            String status,
            UUID commandUid,
            UUID reliableTaskUid,
            UUID edgeUpdateUid,
            int targetAttemptCount,
            int rollbackAttemptCount,
            String installedFirmwareVersion,
            Long installedFirmwareVersionCode,
            String installedFirmwareIdentityHex,
            String errorCode,
            Instant queuedAt,
            Instant completedAt,
            Instant updatedAt) {
    }

    public record RolloutView(
            UUID rolloutUid,
            ReleaseView release,
            String status,
            int batchSize,
            int maximumWaveNo,
            int currentWaveNo,
            String validationHardwareSn,
            String reason,
            String createdBy,
            String promotedBy,
            Instant promotedAt,
            String stoppedBy,
            Instant stoppedAt,
            String stopReason,
            long pendingCount,
            long runningCount,
            long succeededCount,
            long rolledBackCount,
            long failedCount,
            Instant createdAt,
            Instant updatedAt,
            List<DeploymentView> deployments) {

        public RolloutView {
            deployments = List.copyOf(deployments);
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
