package org.enveloping.ecobin.device.api.result;

import java.util.UUID;

public record DeliveryCompleteMeasurement(
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
}
