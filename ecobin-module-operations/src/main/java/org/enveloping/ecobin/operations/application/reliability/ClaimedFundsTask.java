package org.enveloping.ecobin.operations.application.reliability;

import java.time.LocalDateTime;
import java.util.Objects;
import java.util.UUID;

public record ClaimedFundsTask(
        long taskId,
        UUID taskUid,
        long attemptId,
        UUID attemptUid,
        UUID leaseToken,
        long claimedWakeVersion,
        int consecutiveFailureCount,
        int maxAutoAttempts,
        String taskType,
        String targetStableKey,
        LocalDateTime claimedAt) {

    public ClaimedFundsTask {
        if (taskId <= 0 || attemptId <= 0) {
            throw new IllegalArgumentException("task keys must be positive");
        }
        Objects.requireNonNull(taskUid, "taskUid");
        Objects.requireNonNull(attemptUid, "attemptUid");
        Objects.requireNonNull(leaseToken, "leaseToken");
        Objects.requireNonNull(taskType, "taskType");
        Objects.requireNonNull(targetStableKey, "targetStableKey");
        Objects.requireNonNull(claimedAt, "claimedAt");
    }
}
