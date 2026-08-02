package org.enveloping.ecobin.device.api.value;

import java.util.Objects;
import java.util.Set;

/**
 * Interprets the authenticated Orange Pi runtime weight projection used to
 * authorize a new physical operation.
 *
 * <p>{@code STABLE_WINDOW_MEAN} is the native UART v1 stable-measurement
 * shape. {@code LAST_OBSERVED} is the explicitly frozen fixed-frame
 * compatibility shape: the legacy MCU only returns one stable PRE/POST pair
 * at the end of DD/EF and cannot expose the underlying sample window.</p>
 */
public final class DeviceRuntimeWeightPolicy {

    private static final Set<String> START_ELIGIBLE_VALUE_KINDS = Set.of(
            "STABLE_WINDOW_MEAN",
            "LAST_OBSERVED");

    private DeviceRuntimeWeightPolicy() {
    }

    public static boolean isStartEligible(
            String sensorHealth,
            String measurementStatus,
            Boolean valueAvailable,
            Long reportedWeightGrams,
            String valueKind,
            Long runtimeCalibrationVersion,
            Long configuredCalibrationVersion) {
        return "OK".equals(sensorHealth)
                && "STABLE".equals(measurementStatus)
                && Boolean.TRUE.equals(valueAvailable)
                && reportedWeightGrams != null
                && START_ELIGIBLE_VALUE_KINDS.contains(valueKind)
                && Objects.equals(
                        runtimeCalibrationVersion,
                        configuredCalibrationVersion);
    }
}
