package org.enveloping.ecobin.operations.application.reliability;

import java.time.LocalDateTime;
import java.util.Objects;
import java.util.UUID;

public record ClaimedInboxTask(
        UUID taskUid,
        UUID inboxUid,
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
        Objects.requireNonNull(attemptUid, "attemptUid");
        Objects.requireNonNull(leaseToken, "leaseToken");
        Objects.requireNonNull(messageKind, "messageKind");
        Objects.requireNonNull(normalizedPayload, "normalizedPayload");
        Objects.requireNonNull(claimedAt, "claimedAt");
        Objects.requireNonNull(leaseUntil, "leaseUntil");
    }
}
