package org.enveloping.ecobin.device.api.result;

import java.util.Objects;

public record DeviceAcceptanceEvidenceApplyResult(
        long assetId,
        String acceptanceStatus,
        boolean changed) {

    public DeviceAcceptanceEvidenceApplyResult {
        if (assetId <= 0) {
            throw new IllegalArgumentException("assetId must be positive");
        }
        Objects.requireNonNull(acceptanceStatus, "acceptanceStatus");
    }
}
