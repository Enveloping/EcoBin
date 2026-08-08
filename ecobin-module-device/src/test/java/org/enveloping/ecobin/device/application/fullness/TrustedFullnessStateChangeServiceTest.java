package org.enveloping.ecobin.device.application.fullness;

import org.enveloping.ecobin.device.api.result.FullnessSampleMeasurement;
import org.enveloping.ecobin.device.api.result.FullnessStateChangePhysicalFact;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class TrustedFullnessStateChangeServiceTest {

    @Test
    void acceptsFullOnlyWhenConfiguredEvidenceConcludesFull() {
        assertThatCode(() -> TrustedFullnessStateChangeService.validate(
                fact("FULL", "SENSOR_OR_WEIGHT", "BLOCKED",
                        51_200L, 1_200L, 10_000L, true,
                        "STABLE")))
                .doesNotThrowAnyException();
    }

    @Test
    void acceptsNotFullOnlyWhenBothOrModeSourcesAreClear() {
        assertThatCode(() -> TrustedFullnessStateChangeService.validate(
                fact("NOT_FULL", "SENSOR_OR_WEIGHT", "CLEAR",
                        1_200L, 1_200L, 0L, false,
                        "STABLE")))
                .doesNotThrowAnyException();
    }

    @Test
    void rejectsClaimedFullThatDoesNotMatchEvidence() {
        assertThatThrownBy(() ->
                TrustedFullnessStateChangeService.validate(
                        fact("FULL", "SENSOR_OR_WEIGHT", "CLEAR",
                                1_200L, 1_200L, 0L, false,
                                "STABLE")))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("decision differs");
    }

    @Test
    void failedMeasurementCannotChangeFullnessState() {
        assertThatThrownBy(() ->
                TrustedFullnessStateChangeService.validate(
                        fact("FULL", "SENSOR_ONLY", "BLOCKED",
                                51_200L, 1_200L, 10_000L, true,
                                "UNSTABLE")))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("evidence is invalid");
    }

    private static FullnessStateChangePhysicalFact fact(
            String state,
            String mode,
            String sensorValue,
            long totalWeight,
            long baselineWeight,
            long percentHundredths,
            boolean weightFull,
            String measurementStatus) {
        return new FullnessStateChangePhysicalFact(
                UUID.fromString(
                        "10000000-0000-4000-8000-000000000001"),
                UUID.fromString(
                        "10000000-0000-4000-8000-000000000002"),
                "HW-TEST-1",
                1L,
                Instant.parse("2026-08-02T00:00:00Z"),
                "SYNCED",
                "a".repeat(64),
                "b".repeat(64),
                1,
                UUID.fromString(
                        "10000000-0000-4000-8000-000000000003"),
                state,
                "DELIVERY_SESSION",
                UUID.fromString(
                        "10000000-0000-4000-8000-000000000004"),
                mode,
                "DIGITAL_INFRARED",
                sensorValue,
                "FIXED_FRAME_CACHED_FINAL_OBSERVATION",
                new FullnessSampleMeasurement(
                        UUID.fromString(
                                "10000000-0000-4000-8000-000000000005"),
                        measurementStatus,
                        true,
                        totalWeight,
                        "STABLE_WINDOW_MEAN",
                        1_000L,
                        10,
                        4L,
                        "OK",
                        null,
                        1L,
                        1L),
                baselineWeight,
                50_000L,
                percentHundredths,
                weightFull,
                8L,
                "c".repeat(64),
                "d".repeat(64));
    }
}
