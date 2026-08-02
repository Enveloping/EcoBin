package org.enveloping.ecobin.device.api.value;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class DeviceRuntimeWeightPolicyTest {

    @Test
    void acceptsNativeStableWindowMeasurement() {
        assertThat(DeviceRuntimeWeightPolicy.isStartEligible(
                "OK",
                "STABLE",
                true,
                12_500L,
                "STABLE_WINDOW_MEAN",
                4L,
                4L)).isTrue();
    }

    @Test
    void acceptsAuthenticatedFixedFrameLastObservation() {
        assertThat(DeviceRuntimeWeightPolicy.isStartEligible(
                "OK",
                "STABLE",
                true,
                0L,
                "LAST_OBSERVED",
                0L,
                0L)).isTrue();
    }

    @Test
    void stillRejectsUnstableUnavailableOrMismatchedMeasurements() {
        assertThat(DeviceRuntimeWeightPolicy.isStartEligible(
                "OK", "UNSTABLE", true, 12_500L,
                "LAST_OBSERVED", 0L, 0L)).isFalse();
        assertThat(DeviceRuntimeWeightPolicy.isStartEligible(
                "OK", "STABLE", false, null,
                "NONE", 0L, 0L)).isFalse();
        assertThat(DeviceRuntimeWeightPolicy.isStartEligible(
                "OK", "STABLE", true, 12_500L,
                "LAST_OBSERVED", 0L, 1L)).isFalse();
    }
}
