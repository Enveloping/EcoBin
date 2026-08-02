package org.enveloping.ecobin.operations.api.inbox;

import java.util.Objects;
import java.util.UUID;

/**
 * 收件或隔离事务提交后返回给传输适配器的稳定结果。
 */
public record TrustedInboxReceipt(
        TrustedInboxReceiptState state,
        UUID inboxUid,
        UUID taskUid,
        UUID quarantineUid,
        String normalizedContentSha256,
        boolean transportAcknowledgementAllowed) {

    public TrustedInboxReceipt {
        Objects.requireNonNull(state, "state");
        Objects.requireNonNull(normalizedContentSha256, "normalizedContentSha256");
        if (!normalizedContentSha256.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException(
                    "normalizedContentSha256 must be lowercase SHA-256 hex");
        }
        if (!transportAcknowledgementAllowed) {
            throw new IllegalArgumentException(
                    "a returned receipt always represents a committed ACK-safe result");
        }
        if (state == TrustedInboxReceiptState.QUARANTINED) {
            Objects.requireNonNull(quarantineUid, "quarantineUid");
        } else {
            Objects.requireNonNull(inboxUid, "inboxUid");
            if (state != TrustedInboxReceiptState.TELEMETRY_APPLIED) {
                Objects.requireNonNull(taskUid, "taskUid");
            } else if (taskUid != null) {
                throw new IllegalArgumentException(
                        "telemetry receipt must not contain taskUid");
            }
            if (quarantineUid != null) {
                throw new IllegalArgumentException(
                        "accepted receipt must not contain quarantineUid");
            }
        }
    }
}
