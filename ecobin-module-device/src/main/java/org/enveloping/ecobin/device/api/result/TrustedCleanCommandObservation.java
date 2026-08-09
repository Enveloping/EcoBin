package org.enveloping.ecobin.device.api.result;

import java.time.LocalDateTime;
import java.util.Objects;

/**
 * Trusted command evidence for a recycling-owned cleaning operation.
 *
 * <p>{@code occurredAt} is present only when the edge clock was trusted.
 * {@code receivedAt} is the backend receipt time and must not be presented as
 * a physical action time.</p>
 */
public record TrustedCleanCommandObservation(
        long tenantId,
        long organizationId,
        long assetId,
        long cleanOperationId,
        String commandType,
        String stage,
        String errorCode,
        LocalDateTime occurredAt,
        LocalDateTime receivedAt) {

    public TrustedCleanCommandObservation {
        if (tenantId <= 0 || organizationId <= 0
                || assetId <= 0 || cleanOperationId <= 0) {
            throw new IllegalArgumentException(
                    "trusted clean observation keys must be positive");
        }
        Objects.requireNonNull(commandType, "commandType");
        Objects.requireNonNull(stage, "stage");
        Objects.requireNonNull(receivedAt, "receivedAt");
    }
}
