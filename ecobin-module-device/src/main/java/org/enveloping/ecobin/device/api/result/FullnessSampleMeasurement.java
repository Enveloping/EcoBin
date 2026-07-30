package org.enveloping.ecobin.device.api.result;

import java.util.Objects;
import java.util.UUID;

public record FullnessSampleMeasurement(
        UUID measurementUid,
        String status,
        boolean weightValueAvailable,
        Long reportedWeightGrams,
        String weightValueKind,
        long measurementElapsedMs,
        int sampleCount,
        long calibrationVersion,
        String sensorHealth,
        String faultCode,
        long mcuBootId,
        long mcuEventSequence) {

    public FullnessSampleMeasurement {
        Objects.requireNonNull(measurementUid, "measurementUid");
        Objects.requireNonNull(status, "status");
        Objects.requireNonNull(weightValueKind, "weightValueKind");
        Objects.requireNonNull(sensorHealth, "sensorHealth");
    }
}
