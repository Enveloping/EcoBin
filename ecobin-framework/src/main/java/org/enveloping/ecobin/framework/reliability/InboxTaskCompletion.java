package org.enveloping.ecobin.framework.reliability;

import java.util.Objects;
import java.util.UUID;

/**
 * 在权威业务事务末尾提交的 inbox 完成命令。
 */
public record InboxTaskCompletion(
        UUID taskUid,
        UUID inboxUid,
        UUID attemptUid,
        UUID leaseToken,
        long claimedWakeVersion,
        InboxTaskCompletionOutcome outcome,
        long durationMillis) {

    public InboxTaskCompletion {
        Objects.requireNonNull(taskUid, "taskUid");
        Objects.requireNonNull(inboxUid, "inboxUid");
        Objects.requireNonNull(attemptUid, "attemptUid");
        Objects.requireNonNull(leaseToken, "leaseToken");
        Objects.requireNonNull(outcome, "outcome");
        if (claimedWakeVersion < 0) {
            throw new IllegalArgumentException(
                    "claimedWakeVersion must not be negative");
        }
        if (durationMillis < 0) {
            throw new IllegalArgumentException("durationMillis must not be negative");
        }
    }
}
