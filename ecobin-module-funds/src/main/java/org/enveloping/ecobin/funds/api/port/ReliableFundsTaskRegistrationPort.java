package org.enveloping.ecobin.funds.api.port;

import java.time.LocalDateTime;
import java.util.Locale;
import java.util.UUID;

/**
 * funds 在自己的业务事务内登记 operations 可靠任务的窄边界。
 */
public interface ReliableFundsTaskRegistrationPort {

    UUID register(ReliableFundsTaskRegistration registration);

    record ReliableFundsTaskRegistration(
            long tenantId,
            long organizationId,
            String taskType,
            String taskKey,
            String targetType,
            String targetStableKey,
            int payloadSchemaVersion,
            String redactedExecutionSnapshot,
            byte[] payloadSha256,
            int maxAutoAttempts,
            LocalDateTime initialRunAt) {

        public ReliableFundsTaskRegistration {
            if (tenantId <= 0 || organizationId <= 0) {
                throw new IllegalArgumentException("task scope must be positive");
            }
            if (taskType == null
                    || !taskType.matches("[A-Z][A-Z0-9_]{0,63}")) {
                throw new IllegalArgumentException("taskType is invalid");
            }
            if (taskKey != null) {
                taskKey = taskKey.toUpperCase(Locale.ROOT);
            }
            if (taskKey == null
                    || !taskKey.matches("[A-Z0-9_:-]{8,255}")) {
                throw new IllegalArgumentException("taskKey is invalid");
            }
            if (targetType == null || targetType.isBlank()
                    || targetStableKey == null || targetStableKey.isBlank()) {
                throw new IllegalArgumentException("task target is required");
            }
            if (payloadSchemaVersion <= 0
                    || redactedExecutionSnapshot == null
                    || payloadSha256 == null
                    || payloadSha256.length != 32
                    || maxAutoAttempts < 1) {
                throw new IllegalArgumentException("task payload is invalid");
            }
            payloadSha256 = payloadSha256.clone();
        }

        @Override
        public byte[] payloadSha256() {
            return payloadSha256.clone();
        }
    }
}
