package org.enveloping.ecobin.device.api.result;

import java.time.Instant;
import java.util.UUID;

public record DeliveryCompletePhoto(
        String slot,
        String status,
        UUID photoUid,
        String url,
        String sha256,
        Long sizeBytes,
        Instant capturedAt,
        String missingReason) {
}
