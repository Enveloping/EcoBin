package org.enveloping.ecobin.device.api.result;

import java.time.LocalDateTime;
import java.util.Objects;

/**
 * Internal target references issued from a locked trusted device command.
 */
public record TrustedEdgeRestartedWork(
        long tenantId,
        long organizationId,
        long assetId,
        String commandType,
        Long cleanOperationId,
        Long fullnessDetectionId,
        Long baselineMeasurementId,
        LocalDateTime observedAt) {

    public TrustedEdgeRestartedWork {
        if (tenantId <= 0 || organizationId <= 0
                || assetId <= 0) {
            throw new IllegalArgumentException(
                    "trusted work scope keys must be positive");
        }
        Objects.requireNonNull(commandType, "commandType");
        Objects.requireNonNull(observedAt, "observedAt");
    }
}
