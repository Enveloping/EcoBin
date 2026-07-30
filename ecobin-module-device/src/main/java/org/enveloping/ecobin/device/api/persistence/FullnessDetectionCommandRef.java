package org.enveloping.ecobin.device.api.persistence;

import java.util.function.Function;

/**
 * Single-use, transaction-bound relation from a recycling-owned fullness
 * detection to the device command that samples it.
 */
public interface FullnessDetectionCommandRef {

    <T> T withForeignKeysOnce(
            Function<ForeignKeys, T> function);

    record ForeignKeys(
            long tenantKey,
            long organizationKey,
            long deploymentKey,
            long portKey,
            long detectionKey,
            long deviceConfigVersionKey,
            long portConfigSnapshotKey) {

        public ForeignKeys {
            if (tenantKey <= 0
                    || organizationKey <= 0
                    || deploymentKey <= 0
                    || portKey <= 0
                    || detectionKey <= 0
                    || deviceConfigVersionKey <= 0
                    || portConfigSnapshotKey <= 0) {
                throw new IllegalArgumentException(
                        "fullness detection foreign keys must be positive");
            }
        }
    }
}
