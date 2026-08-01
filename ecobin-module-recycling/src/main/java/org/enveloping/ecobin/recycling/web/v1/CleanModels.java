package org.enveloping.ecobin.recycling.web.v1;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class CleanModels {

    private CleanModels() {
    }

    public record StartCleanOperationRequest(
            @NotBlank
            @Size(min = 8, max = 64)
            String installedBagQr) {
    }

    public record CleanOperationAccepted(
            UUID operationId,
            UUID resourceId,
            UUID operationUid,
            String status,
            long version,
            int portNo,
            String installedBagQr,
            Instant startAuthorizationExpiresAt,
            String statusUrl,
            long recommendedPollAfterMs,
            List<String> nextActions) {
    }

    public record CleanOperationView(
            UUID operationUid,
            String status,
            long version,
            String deploymentCode,
            int portNo,
            String removedBagQr,
            String installedBagQr,
            boolean firstUnlockMayHaveExecuted,
            boolean cleanLockDeenergizedConfirmed,
            boolean cleanerPhysicalCloseConfirmed,
            Instant startAuthorizationExpiresAt,
            Instant executionDeadlineAt,
            Instant completedAt,
            String cleanRecordNo,
            Long recommendedPollAfterMs,
            List<String> nextActions) {
    }

    public record CleanOptionsView(
            String deploymentCode,
            String displayName,
            String address,
            boolean deviceBusy,
            List<RecoverableCleanOperation> recoverableOperations,
            Instant asOf,
            List<CleanPortOption> ports) {
    }

    public record RecoverableCleanOperation(
            UUID operationUid,
            int portNo,
            String status,
            String statusUrl) {
    }

    public record CleanPortOption(
            int portNo,
            String displayName,
            String currentBagQr,
            String fullnessStatus,
            String fullnessPercent,
            boolean cleaningAllowed,
            List<String> blockers) {
    }
}
