package org.enveloping.ecobin.recycling.web.v1;

import java.time.Instant;

public final class CleanDeviceModels {

    private CleanDeviceModels() {
    }

    public record CleanDeviceItem(
            String deviceCode,
            String displayName,
            String address,
            String connectionStatus,
            int portCount,
            Instant lastDeliveryAt,
            Instant lastCleanAt,
            int fullPortCount,
            Instant oldestFullSince) {
    }
}
