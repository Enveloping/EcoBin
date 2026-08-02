package org.enveloping.ecobin.recycling.application.fullness;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class ApplyFullnessStateChangedServiceTest {

    private static final String BAG_UID =
            "10000000-0000-4000-8000-000000000001";

    @Test
    void rejectsFactWhoseSourceOrReportedBagIsNoLongerCurrent() {
        assertThat(disposition(42L, BAG_UID, 41L, BAG_UID,
                42L, 10L, 11L, "NOT_FULL", "FULL"))
                .isEqualTo("STALE_BAG");
        assertThat(disposition(42L, BAG_UID, 42L,
                "10000000-0000-4000-8000-000000000099",
                42L, 10L, 11L, "NOT_FULL", "FULL"))
                .isEqualTo("STALE_BAG");
    }

    @Test
    void ignoresOlderOrRepeatedSequenceForSameBag() {
        assertThat(disposition(42L, BAG_UID, 42L, BAG_UID,
                42L, 10L, 10L, "NOT_FULL", "FULL"))
                .isEqualTo("STALE_SEQUENCE");
        assertThat(disposition(42L, BAG_UID, 42L, BAG_UID,
                42L, 10L, 9L, "NOT_FULL", "FULL"))
                .isEqualTo("STALE_SEQUENCE");
    }

    @Test
    void recordsNoStateChangeWithoutFlippingCurrentBag() {
        assertThat(disposition(42L, BAG_UID, 42L, BAG_UID,
                42L, 10L, 11L, "FULL", "FULL"))
                .isEqualTo("NO_STATE_CHANGE");
    }

    @Test
    void appliesNewStateAndTreatsMismatchedCapacityBagAsUninitialized() {
        assertThat(disposition(42L, BAG_UID, 42L, BAG_UID,
                42L, 10L, 11L, "NOT_FULL", "FULL"))
                .isEqualTo("APPLIED");
        assertThat(disposition(42L, BAG_UID, 42L, BAG_UID,
                41L, 99L, 1L, "FULL", "FULL"))
                .isEqualTo("APPLIED");
    }

    private static String disposition(
            long currentBagId,
            String currentBagUid,
            long sourceBagId,
            String reportedBagUid,
            Long capacityBagId,
            Long lastEdgeSequence,
            long reportedEdgeSequence,
            String currentState,
            String reportedState) {
        return ApplyFullnessStateChangedService.disposition(
                currentBagId,
                currentBagUid,
                sourceBagId,
                reportedBagUid,
                capacityBagId,
                lastEdgeSequence,
                reportedEdgeSequence,
                currentState,
                reportedState);
    }
}
