package org.enveloping.ecobin.operations.web.v1;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public final class OperationsModels {

    private OperationsModels() { }

    public record PageData<T>(
            List<T> items, int page, int pageSize, long total) {
        public PageData { items = List.copyOf(items); }
    }

    public record CursorPage<T>(List<T> items, String nextCursor) {
        public CursorPage { items = List.copyOf(items); }
    }

    public record ReliableTaskView(
            UUID taskUid,
            String taskType,
            String taskKind,
            String executionLane,
            String state,
            long version,
            String scopeKind,
            String targetType,
            String targetKey,
            Instant nextRunAt,
            boolean leased,
            Instant leaseUntil,
            int maxAutoAttempts,
            long attemptCount,
            int consecutiveFailureCount,
            long wakeVersion,
            long handledWakeVersion,
            String blockedReasonCode,
            String blockedDiagnostic,
            UUID correlationId,
            UUID causationId,
            Instant createdAt,
            Instant updatedAt,
            List<String> nextActions) { }

    public record TaskAttemptView(
            UUID attemptUid,
            long attemptNo,
            String actionKind,
            String technicalResult,
            Instant claimedAt,
            Instant externalCallMayHaveStartedAt,
            Instant resultRecordedAt,
            Integer httpStatus,
            String externalApiErrorCode,
            Long durationMs,
            String diagnostic) { }

    public record ResumeTaskRequest(
            @NotNull Long expectedVersion,
            @NotNull Boolean causeFixedConfirmed,
            @Size(max = 500) String reason) { }

    public record AcceptedOperation(
            UUID operationId,
            UUID resourceId,
            UUID taskUid,
            String state,
            long version,
            String statusUrl,
            long recommendedPollAfterMs) { }

    public record QuarantineView(
            UUID quarantineUid,
            String state,
            long version,
            String scopeKind,
            String reasonCode,
            String sourceNamespace,
            String sourcePrincipalSummary,
            String externalMessageSummary,
            String rawTransportSha256,
            String normalizedContentSha256,
            String diagnostic,
            Instant firstDetectedAt,
            Instant lastDetectedAt,
            long discoveryCount,
            Instant acknowledgedAt) { }

    public record VersionedReasonRequest(
            @NotNull Long expectedVersion,
            @Size(max = 500) String reason) { }

    public record VersionedOperationResult(
            UUID operationId,
            UUID resourceId,
            String state,
            long version) { }

    public record AuditLogView(
            UUID auditUid,
            UUID requestUid,
            UUID operationUid,
            String scopeKind,
            String tenantCode,
            String organizationCode,
            String actorKind,
            UUID actorUid,
            String actorDisplayName,
            String actionCode,
            String targetType,
            String targetKey,
            String entryChannel,
            String result,
            String reason,
            Map<String, Object> safeChangeSummary,
            UUID correlationId,
            UUID causationId,
            Instant occurredAt) { }

    public record AlertView(
            UUID alertUid,
            String state,
            long version,
            String category,
            String alertCode,
            String currentSeverity,
            String highestSeverity,
            String scopeKind,
            String tenantCode,
            String organizationCode,
            String sourceKind,
            String sourceType,
            String sourceKey,
            Instant firstDetectedAt,
            Instant lastDetectedAt,
            long discoveryCount,
            boolean acknowledged,
            String acknowledgedBy,
            Instant acknowledgedAt,
            Instant resolvedAt,
            Map<String, Object> displayParameters,
            List<String> nextActions) { }
}
