package org.enveloping.ecobin.recycling.application.fullness;

import org.enveloping.ecobin.device.api.result.FullnessSampleMeasurement;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

class FullnessDecisionPolicyTest {

    @Test
    void weightOnlyUsesNetWeightAndIgnoresInfrared() {
        var full = decide(
                "WEIGHT_ONLY",
                1_000L,
                5_000L,
                "BLOCKED",
                stableWeight(6_000L));

        assertThat(full.infraredStatus()).isEqualTo("NOT_REQUIRED");
        assertThat(full.infraredFull()).isNull();
        assertThat(full.weightStatus()).isEqualTo("RELIABLE");
        assertThat(full.rawNetWeightGrams()).isEqualTo(5_000L);
        assertThat(full.displayedPercent())
                .isEqualByComparingTo(new BigDecimal("100.00"));
        assertThat(full.conclusion()).isEqualTo("FULL");
        assertThat(full.fullReason()).isEqualTo("WEIGHT");
        assertThat(full.failureCode()).isNull();

        var notFull = decide(
                "WEIGHT_ONLY",
                1_000L,
                5_000L,
                "BLOCKED",
                stableWeight(5_999L));

        assertThat(notFull.displayedPercent())
                .isEqualByComparingTo(new BigDecimal("99.98"));
        assertThat(notFull.conclusion()).isEqualTo("NOT_FULL");
    }

    @Test
    void infraredOnlyDoesNotRequireWeightOrBaseline() {
        var full = decide(
                "INFRARED_ONLY",
                null,
                5_000L,
                "BLOCKED",
                failedWeight());

        assertThat(full.infraredStatus()).isEqualTo("RELIABLE");
        assertThat(full.weightStatus()).isEqualTo("NOT_REQUIRED");
        assertThat(full.displayedPercent()).isNull();
        assertThat(full.conclusion()).isEqualTo("FULL");
        assertThat(full.fullReason()).isEqualTo("INFRARED");

        var notFull = decide(
                "INFRARED_ONLY",
                null,
                5_000L,
                "CLEAR",
                failedWeight());

        assertThat(notFull.conclusion()).isEqualTo("NOT_FULL");
        assertThat(notFull.failureCode()).isNull();
    }

    @Test
    void combinedModeAcceptsOneReliableFullSourceButNotOneReliableClearSource() {
        var infraredFull = decide(
                "INFRARED_OR_WEIGHT",
                null,
                5_000L,
                "BLOCKED",
                failedWeight());

        assertThat(infraredFull.weightStatus()).isEqualTo("FAILED");
        assertThat(infraredFull.conclusion()).isEqualTo("FULL");
        assertThat(infraredFull.fullReason()).isEqualTo("INFRARED");
        assertThat(infraredFull.failureCode()).isNull();

        var inconclusive = decide(
                "INFRARED_OR_WEIGHT",
                null,
                5_000L,
                "CLEAR",
                failedWeight());

        assertThat(inconclusive.conclusion()).isEqualTo("SOURCE_FAILED");
        assertThat(inconclusive.failureCode())
                .isEqualTo("WEIGHT_BASELINE_UNAVAILABLE");

        var bothFull = decide(
                "INFRARED_OR_WEIGHT",
                1_000L,
                5_000L,
                "BLOCKED",
                stableWeight(6_000L));

        assertThat(bothFull.conclusion()).isEqualTo("FULL");
        assertThat(bothFull.fullReason()).isEqualTo("BOTH");
    }

    @Test
    void netWeightCannotFallBelowZero() {
        var result = decide(
                "WEIGHT_ONLY",
                1_000L,
                5_000L,
                "CLEAR",
                stableWeight(500L));

        assertThat(result.rawNetWeightGrams()).isEqualTo(-500L);
        assertThat(result.displayedPercent())
                .isEqualByComparingTo(new BigDecimal("0.00"));
        assertThat(result.conclusion()).isEqualTo("NOT_FULL");
    }

    private static ApplyFullnessSampleCompleteService.SampleCalculation decide(
            String mode,
            Long baselineWeightGrams,
            long fullWeightGrams,
            String infraredValue,
            FullnessSampleMeasurement measurement) {
        return ApplyFullnessSampleCompleteService.decide(
                mode,
                baselineWeightGrams,
                fullWeightGrams,
                infraredValue,
                measurement);
    }

    private static FullnessSampleMeasurement stableWeight(long weightGrams) {
        return new FullnessSampleMeasurement(
                UUID.randomUUID(),
                "STABLE",
                true,
                weightGrams,
                "STABLE_WINDOW_MEAN",
                200L,
                4,
                1L,
                "OK",
                null,
                101L,
                1L);
    }

    private static FullnessSampleMeasurement failedWeight() {
        return new FullnessSampleMeasurement(
                UUID.randomUUID(),
                "TIMEOUT",
                false,
                null,
                "NONE",
                2_000L,
                0,
                1L,
                "FAILED",
                "WEIGHT_TIMEOUT",
                101L,
                1L);
    }
}
