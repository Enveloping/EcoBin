package org.enveloping.ecobin.recycling.application.deliveryquery;

import java.math.BigDecimal;
import java.util.Objects;

/**
 * Recycling-owned availability facts for one device port.
 *
 * <p>Missing capacity state is represented explicitly. A missing percentage
 * remains {@code null}; the adapter never fabricates zero fullness.</p>
 */
public record DeliveryPortBusinessFacts(
        int portNo,
        boolean currentBagPresent,
        BaselineState baselineState,
        BigDecimal displayedFullnessPercent,
        DetectionGate detectionGate,
        ConfirmedFullnessState confirmedFullnessState,
        boolean baselineRemeasurementActive,
        boolean cleanOperationActive) {

    public DeliveryPortBusinessFacts {
        if (portNo < 1 || portNo > 6) {
            throw new IllegalArgumentException(
                    "portNo must be between 1 and 6");
        }
        Objects.requireNonNull(baselineState, "baselineState");
        Objects.requireNonNull(detectionGate, "detectionGate");
        Objects.requireNonNull(
                confirmedFullnessState,
                "confirmedFullnessState");
        boolean capacityMissing =
                baselineState == BaselineState.MISSING;
        if (capacityMissing
                != (detectionGate == DetectionGate.MISSING)
                || capacityMissing
                != (confirmedFullnessState
                == ConfirmedFullnessState.MISSING)) {
            throw new IllegalArgumentException(
                    "capacity missing states must be consistent");
        }
        if (capacityMissing && displayedFullnessPercent != null) {
            throw new IllegalArgumentException(
                    "missing capacity cannot have a fullness percentage");
        }
    }

    public enum BaselineState {
        MISSING,
        UNINITIALIZED,
        VALID,
        INVALID
    }

    public enum DetectionGate {
        MISSING,
        UNKNOWN,
        PENDING,
        IN_PROGRESS,
        READY,
        FAILED
    }

    public enum ConfirmedFullnessState {
        MISSING,
        UNKNOWN,
        NOT_FULL,
        FULL
    }
}
