package org.enveloping.ecobin.framework.reliability;

import java.util.Arrays;
import java.time.LocalDateTime;
import java.util.Objects;
import java.util.UUID;

public record ReliableDeviceTaskRegistration(
        String taskType,
        String taskKey,
        String targetType,
        String targetStableKey,
        DeviceCommandTaskRef sourceCommand,
        int payloadSchemaVersion,
        String redactedExecutionSnapshot,
        byte[] payloadSha256,
        UUID correlationUid,
        UUID causationUid,
        int maxAutoAttempts,
        boolean supersedePriorPendingTasks,
        LocalDateTime initialRunAt) {

    public ReliableDeviceTaskRegistration {
        requireCode(taskType, "taskType");
        Objects.requireNonNull(taskKey, "taskKey");
        if (!taskKey.matches("[A-Z0-9_:-]{8,255}")) {
            throw new IllegalArgumentException(
                    "taskKey must be a stable uppercase key");
        }
        requireCode(targetType, "targetType");
        Objects.requireNonNull(targetStableKey, "targetStableKey");
        Objects.requireNonNull(sourceCommand, "sourceCommand");
        if (payloadSchemaVersion <= 0) {
            throw new IllegalArgumentException(
                    "payloadSchemaVersion must be positive");
        }
        Objects.requireNonNull(
                redactedExecutionSnapshot, "redactedExecutionSnapshot");
        Objects.requireNonNull(payloadSha256, "payloadSha256");
        if (payloadSha256.length != 32) {
            throw new IllegalArgumentException(
                    "payloadSha256 must contain 32 bytes");
        }
        payloadSha256 = Arrays.copyOf(payloadSha256, payloadSha256.length);
        if (maxAutoAttempts < 1 || maxAutoAttempts > 1000) {
            throw new IllegalArgumentException(
                    "maxAutoAttempts must be between 1 and 1000");
        }
    }

    @Override
    public byte[] payloadSha256() {
        return Arrays.copyOf(payloadSha256, payloadSha256.length);
    }

    private static void requireCode(String value, String field) {
        Objects.requireNonNull(value, field);
        if (!value.matches("[A-Z][A-Z0-9_]{0,63}")) {
            throw new IllegalArgumentException(
                    field + " must be an uppercase stable code");
        }
    }
}
