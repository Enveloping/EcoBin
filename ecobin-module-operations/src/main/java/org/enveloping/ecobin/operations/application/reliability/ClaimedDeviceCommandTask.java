package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;

import java.time.LocalDateTime;
import java.util.Objects;
import java.util.UUID;

public record ClaimedDeviceCommandTask(
        UUID taskUid,
        UUID commandUid,
        UUID attemptUid,
        UUID leaseToken,
        long claimedWakeVersion,
        String commandType,
        String hardwareSn,
        String semanticEnvelopeJson,
        byte[] semanticEnvelopeSha256,
        LocalDateTime claimedAt,
        LocalDateTime leaseUntil) {

    public ClaimedDeviceCommandTask {
        Objects.requireNonNull(taskUid, "taskUid");
        Objects.requireNonNull(commandUid, "commandUid");
        Objects.requireNonNull(attemptUid, "attemptUid");
        Objects.requireNonNull(leaseToken, "leaseToken");
        Objects.requireNonNull(commandType, "commandType");
        Objects.requireNonNull(hardwareSn, "hardwareSn");
        Objects.requireNonNull(
                semanticEnvelopeJson, "semanticEnvelopeJson");
        Objects.requireNonNull(
                semanticEnvelopeSha256, "semanticEnvelopeSha256");
        if (semanticEnvelopeSha256.length != 32) {
            throw new IllegalArgumentException(
                    "semanticEnvelopeSha256 must contain 32 bytes");
        }
        semanticEnvelopeSha256 = semanticEnvelopeSha256.clone();
        Objects.requireNonNull(claimedAt, "claimedAt");
        Objects.requireNonNull(leaseUntil, "leaseUntil");
    }

    @Override
    public byte[] semanticEnvelopeSha256() {
        return semanticEnvelopeSha256.clone();
    }

    public DeviceCommandSubmission submission() {
        return new DeviceCommandSubmission(
                taskUid,
                commandUid,
                commandType,
                hardwareSn,
                semanticEnvelopeJson,
                semanticEnvelopeSha256);
    }
}
