package org.enveloping.ecobin.recycling.application.portgeneration;

import java.util.Objects;

/**
 * Compares mutable capacity/baseline projections with the bag that currently
 * occupies a port.
 *
 * <p>The factory-installed bag is deliberately absent: after a clean, only
 * the current occupancy defines the active bag generation.</p>
 */
public final class CurrentPortGenerationPolicy {

    private CurrentPortGenerationPolicy() {
    }

    public static boolean requiresWeightBaseline(String fullnessMode) {
        return switch (Objects.requireNonNull(
                fullnessMode,
                "fullnessMode")) {
            case "INFRARED_ONLY" -> false;
            case "WEIGHT_ONLY", "INFRARED_OR_WEIGHT" -> true;
            default -> throw new IllegalArgumentException(
                    "unsupported fullnessMode: " + fullnessMode);
        };
    }

    public static boolean isCurrentBagFull(
            long occupiedBagId,
            Long capacityBagId,
            String confirmedFullnessState) {
        return Objects.equals(capacityBagId, occupiedBagId)
                && "FULL".equals(confirmedFullnessState);
    }

    public static boolean hasValidCurrentWeightBaseline(
            long occupiedBagId,
            Long capacityBagId,
            String capacityBaselineState,
            Long capacityBaselineId,
            Long capacityBaselineWeightGrams,
            Long baselineId,
            Long baselineBagId,
            Long baselineWeightGrams) {
        return "VALID".equals(capacityBaselineState)
                && Objects.equals(capacityBagId, occupiedBagId)
                && capacityBaselineId != null
                && capacityBaselineWeightGrams != null
                && Objects.equals(baselineId, capacityBaselineId)
                && Objects.equals(baselineBagId, occupiedBagId)
                && Objects.equals(
                        baselineWeightGrams,
                        capacityBaselineWeightGrams);
    }
}
