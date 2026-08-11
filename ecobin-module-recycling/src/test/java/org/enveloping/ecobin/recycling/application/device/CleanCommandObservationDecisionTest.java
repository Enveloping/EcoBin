package org.enveloping.ecobin.recycling.application.device;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.enveloping.ecobin.recycling.application.device.CleanCommandObservationDecision.Action;

class CleanCommandObservationDecisionTest {

    @Test
    void commandTimesUseOnlyDeviceValuesBoundedByBusinessAndReceipt() {
        LocalDateTime createdAt = LocalDateTime.of(
                2026, 8, 11, 12, 0);
        LocalDateTime receivedAt = createdAt.plusSeconds(5);
        LocalDateTime valid = createdAt.plusSeconds(2);

        assertThat(ApplyCleanCommandObservationService
                .trustedOperationTime(valid, createdAt, receivedAt))
                .isEqualTo(valid);
        assertThat(ApplyCleanCommandObservationService
                .trustedOperationTime(null, createdAt, receivedAt))
                .isEqualTo(receivedAt);
        assertThat(ApplyCleanCommandObservationService
                .trustedOperationTime(
                        createdAt.minusSeconds(1), createdAt, receivedAt))
                .isEqualTo(receivedAt);
        assertThat(ApplyCleanCommandObservationService
                .trustedOperationTime(
                        receivedAt.plusSeconds(1), createdAt, receivedAt))
                .isEqualTo(receivedAt);
    }

    @Test
    void projectsTrustedStartStagesToMonotonicSafetyActions() {
        assertThat(decide("PREPARED", false, "RECEIVED", null))
                .isEqualTo(Action.NONE);
        assertThat(decide("PREPARED", false, "ACCEPTED", null))
                .isEqualTo(Action.MARK_EDGE_SAVED);
        assertThat(decide("EDGE_SAVED", false, "MCU_ACCEPTED", null))
                .isEqualTo(Action.MARK_IN_PROGRESS);
        assertThat(decide("EDGE_SAVED", false, "REJECTED", "BUSY"))
                .isEqualTo(Action.END_BEFORE_UNLOCK);
        assertThat(decide("IN_PROGRESS", true, "REJECTED", "LATE"))
                .isEqualTo(Action.NONE);
        assertThat(decide("EDGE_SAVED", false, "FAILED", "UART_TIMEOUT"))
                .isEqualTo(Action.REQUIRE_RECOVERY);
    }

    @Test
    void leavesEdgeRestartForTheExistingAbortAndInterlockService() {
        assertThat(decide(
                "IN_PROGRESS", true, "FAILED", "EDGE_RESTARTED"))
                .isEqualTo(Action.NONE);
    }

    @Test
    void terminalOperationsNeverRegressOnLateEvidence() {
        for (String status : new String[]{
                "PRE_UNLOCK_ENDED", "COMPLETED", "ABORTED"}) {
            assertThat(decide(status, true, "ACCEPTED", null))
                    .isEqualTo(Action.NONE);
        }
    }

    @Test
    void ignoresCommandsThatDoNotOwnTheCleaningStateMachine() {
        assertThat(CleanCommandObservationDecision.decide(
                "START_DELIVERY_SESSION",
                "PREPARED",
                false,
                "MCU_ACCEPTED",
                null)).isEqualTo(Action.NONE);
    }

    @Test
    void rejectsUnknownObservationStages() {
        assertThatThrownBy(() -> decide(
                "PREPARED", false, "FUTURE_STAGE", null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    private static Action decide(
            String status,
            boolean firstUnlock,
            String stage,
            String errorCode) {
        return CleanCommandObservationDecision.decide(
                "START_CLEAN_OPERATION",
                status,
                firstUnlock,
                stage,
                errorCode);
    }
}
