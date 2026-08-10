package org.enveloping.ecobin.recycling.application.portgeneration;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class CurrentPortGenerationPolicyTest {

    @Test
    void onlyWeightBasedModesRequireAWeightBaseline() {
        assertThat(CurrentPortGenerationPolicy
                .requiresWeightBaseline("INFRARED_ONLY")).isFalse();
        assertThat(CurrentPortGenerationPolicy
                .requiresWeightBaseline("WEIGHT_ONLY")).isTrue();
        assertThat(CurrentPortGenerationPolicy
                .requiresWeightBaseline("INFRARED_OR_WEIGHT")).isTrue();
    }

    @Test
    void baselineIsValidOnlyWhenEveryIdentityMatchesTheCurrentBag() {
        assertThat(validBaseline(41L, 41L, 51L, 51L, 100L, 100L))
                .isTrue();
        assertThat(validBaseline(41L, 40L, 51L, 51L, 100L, 100L))
                .isFalse();
        assertThat(validBaseline(41L, 41L, 51L, 51L, 100L, 99L))
                .isFalse();
        assertThat(validBaseline(41L, 41L, 51L, 52L, 100L, 100L))
                .isFalse();
        assertThat(CurrentPortGenerationPolicy
                .hasValidCurrentWeightBaseline(
                        41L,
                        41L,
                        "VALID",
                        51L,
                        100L,
                        51L,
                        40L,
                        100L)).isFalse();
    }

    private static boolean validBaseline(
            long occupiedBagId,
            Long capacityBagId,
            Long capacityBaselineId,
            Long baselineId,
            Long capacityWeight,
            Long baselineWeight) {
        return CurrentPortGenerationPolicy
                .hasValidCurrentWeightBaseline(
                        occupiedBagId,
                        capacityBagId,
                        "VALID",
                        capacityBaselineId,
                        capacityWeight,
                        baselineId,
                        baselineId == null ? null : occupiedBagId,
                        baselineWeight);
    }
}
