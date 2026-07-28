package org.enveloping.ecobin.operations.application.reliability;

import java.time.LocalDateTime;
import java.util.Objects;
import java.util.UUID;

public record ClaimedInboxTask(
        UUID taskUid,
        UUID inboxUid,
        long inboxId,
        String scopeKind,
        Long tenantId,
        Long organizationId,
        UUID attemptUid,
        UUID leaseToken,
        long claimedWakeVersion,
        String messageKind,
        int normalizedSchemaVersion,
        String normalizedPayload,
        LocalDateTime claimedAt,
        LocalDateTime leaseUntil) {

    public ClaimedInboxTask {
        Objects.requireNonNull(taskUid, "taskUid");
        Objects.requireNonNull(inboxUid, "inboxUid");
        if (inboxId <= 0) {
            throw new IllegalArgumentException("inboxId must be positive");
        }
        Objects.requireNonNull(scopeKind, "scopeKind");
        if ("ORGANIZATION".equals(scopeKind)) {
            if (tenantId == null || tenantId <= 0
                    || organizationId == null || organizationId <= 0) {
                throw new IllegalArgumentException(
                        "organization inbox requires positive scope keys");
            }
        } else if (!"PLATFORM".equals(scopeKind)
                || tenantId != null || organizationId != null) {
            throw new IllegalArgumentException(
                    "claimed inbox has an unsupported scope shape");
        }
        Objects.requireNonNull(attemptUid, "attemptUid");
        Objects.requireNonNull(leaseToken, "leaseToken");
        Objects.requireNonNull(messageKind, "messageKind");
        Objects.requireNonNull(normalizedPayload, "normalizedPayload");
        Objects.requireNonNull(claimedAt, "claimedAt");
        Objects.requireNonNull(leaseUntil, "leaseUntil");
    }
}
