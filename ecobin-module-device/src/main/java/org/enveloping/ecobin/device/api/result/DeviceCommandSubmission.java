package org.enveloping.ecobin.device.api.result;

import java.util.Objects;
import java.util.UUID;

public record DeviceCommandSubmission(
        UUID taskUid,
        UUID commandUid,
        long attemptSequence,
        String commandType,
        String hardwareSn,
        String semanticEnvelopeJson,
        byte[] semanticEnvelopeSha256) {

    public DeviceCommandSubmission {
        Objects.requireNonNull(taskUid, "taskUid");
        Objects.requireNonNull(commandUid, "commandUid");
        if (attemptSequence < 1) {
            throw new IllegalArgumentException(
                    "attemptSequence must be positive");
        }
        commandType = requireText(commandType, "commandType");
        hardwareSn = requireText(hardwareSn, "hardwareSn");
        semanticEnvelopeJson =
                requireText(semanticEnvelopeJson, "semanticEnvelopeJson");
        Objects.requireNonNull(
                semanticEnvelopeSha256, "semanticEnvelopeSha256");
        if (semanticEnvelopeSha256.length != 32) {
            throw new IllegalArgumentException(
                    "semanticEnvelopeSha256 must contain 32 bytes");
        }
        semanticEnvelopeSha256 = semanticEnvelopeSha256.clone();
    }

    @Override
    public byte[] semanticEnvelopeSha256() {
        return semanticEnvelopeSha256.clone();
    }

    private static String requireText(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(name + " must not be blank");
        }
        return value;
    }
}
