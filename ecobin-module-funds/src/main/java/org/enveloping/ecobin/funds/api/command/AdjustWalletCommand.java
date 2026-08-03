package org.enveloping.ecobin.funds.api.command;

import org.enveloping.ecobin.identity.api.persistence.WalletAdjustmentTargetRef;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

public record AdjustWalletCommand(
        UUID operationUid,
        WalletAdjustmentTargetRef targetRef,
        boolean platformActor,
        UUID actorUid,
        UUID sessionUid,
        String actorDisplayName,
        long deltaCent,
        long expectedWalletVersion,
        String reason,
        long currentStopThresholdCent,
        Instant occurredAt) {

    public AdjustWalletCommand {
        Objects.requireNonNull(operationUid, "operationUid");
        Objects.requireNonNull(targetRef, "targetRef");
        Objects.requireNonNull(actorUid, "actorUid");
        Objects.requireNonNull(sessionUid, "sessionUid");
        if (actorDisplayName == null || actorDisplayName.isBlank()) {
            throw new IllegalArgumentException(
                    "actorDisplayName must not be blank");
        }
        if (deltaCent == 0) {
            throw new IllegalArgumentException("deltaCent must not be zero");
        }
        if (expectedWalletVersion < 0) {
            throw new IllegalArgumentException(
                    "expectedWalletVersion must not be negative");
        }
        if (currentStopThresholdCent >= 0) {
            throw new IllegalArgumentException(
                    "currentStopThresholdCent must be negative");
        }
        if (reason != null && reason.length() > 500) {
            throw new IllegalArgumentException("reason is too long");
        }
        Objects.requireNonNull(occurredAt, "occurredAt");
    }
}
