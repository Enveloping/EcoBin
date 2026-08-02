package org.enveloping.ecobin.device.api.result;

import java.util.Objects;

public record DeviceTransportPresenceApplyResult(
        long assetId,
        String status,
        boolean changed) {

    public DeviceTransportPresenceApplyResult {
        if (assetId <= 0) {
            throw new IllegalArgumentException("assetId must be positive");
        }
        Objects.requireNonNull(status, "status");
        if (!status.equals("ONLINE") && !status.equals("OFFLINE")) {
            throw new IllegalArgumentException(
                    "status must be ONLINE or OFFLINE");
        }
    }
}
