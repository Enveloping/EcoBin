package org.enveloping.ecobin.device.application.delivery;

import org.enveloping.ecobin.device.api.result.DeliveryCompleteMeasurement;
import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class TrustedDeliveryCompletionWeightPolicyTest {

    @Test
    void acceptsOneStableFixedFrameMeasurementWithinFrozenRange() {
        DeliveryCompleteMeasurement measurement = measurement(1, 12_500, 0);

        assertTrue(TrustedDeliveryCompletionService
                .measurementMatchesFrozenPort(
                        measurement,
                        0,
                        0,
                        350_000));
    }

    @Test
    void rejectsMissingSampleWrongCalibrationAndOutOfRangeWeight() {
        assertFalse(TrustedDeliveryCompletionService
                .measurementMatchesFrozenPort(
                        measurement(0, 12_500, 0),
                        0,
                        0,
                        350_000));
        assertFalse(TrustedDeliveryCompletionService
                .measurementMatchesFrozenPort(
                        measurement(1, 12_500, 1),
                        0,
                        0,
                        350_000));
        assertFalse(TrustedDeliveryCompletionService
                .measurementMatchesFrozenPort(
                        measurement(1, 350_001, 0),
                        0,
                        0,
                        350_000));
    }

    private static DeliveryCompleteMeasurement measurement(
            int sampleCount,
            long weightGrams,
            long calibrationVersion) {
        return new DeliveryCompleteMeasurement(
                UUID.randomUUID(),
                "STABLE",
                true,
                weightGrams,
                "STABLE_WINDOW_MEAN",
                0,
                sampleCount,
                calibrationVersion,
                "OK",
                null,
                42,
                1);
    }
}
