package org.enveloping.ecobin.device.application.delivery;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.enveloping.ecobin.device.application.delivery.DeliveryCommandObservationDecision.Action;

class DeliveryCommandObservationDecisionTest {

    @Test
    void commandTimesUseOnlyDeviceValuesBoundedByBusinessAndReceipt() {
        LocalDateTime createdAt = LocalDateTime.of(
                2026, 8, 11, 12, 0);
        LocalDateTime receivedAt = createdAt.plusSeconds(5);
        LocalDateTime valid = createdAt.plusSeconds(2);

        assertThat(ApplyDeliveryCommandObservationService
                .trustedOperationTime(valid, createdAt, receivedAt))
                .isEqualTo(valid);
        assertThat(ApplyDeliveryCommandObservationService
                .trustedOperationTime(null, createdAt, receivedAt))
                .isEqualTo(receivedAt);
        assertThat(ApplyDeliveryCommandObservationService
                .trustedOperationTime(
                        createdAt.minusSeconds(1), createdAt, receivedAt))
                .isEqualTo(receivedAt);
        assertThat(ApplyDeliveryCommandObservationService
                .trustedOperationTime(
                        receivedAt.plusSeconds(1), createdAt, receivedAt))
                .isEqualTo(receivedAt);
    }

    @Test
    void projectsTrustedStartStagesWithoutReplayingPhysicalWork() {
        assertThat(decide("AUTHORIZATION_QUEUED", "RECEIVED", null))
                .isEqualTo(Action.NONE);
        assertThat(decide("AUTHORIZATION_QUEUED", "ACCEPTED", null))
                .isEqualTo(Action.MARK_EDGE_ACCEPTED);
        assertThat(decide("AUTHORIZATION_QUEUED", "MCU_ACCEPTED", null))
                .isEqualTo(Action.MARK_IN_PROGRESS);
        assertThat(decide(
                "AUTHORIZATION_QUEUED", "REJECTED", "DEVICE_BUSY"))
                .isEqualTo(Action.END_BEFORE_OPEN);
        assertThat(decide(
                "IN_PROGRESS", "PRE_START_FAILED", "LATE"))
                .isEqualTo(Action.NONE);
        assertThat(decide(
                "IN_PROGRESS", "FAILED", "UART_TIMEOUT"))
                .isEqualTo(Action.REQUIRE_RECOVERY);
        assertThat(decide(
                "RESULT_PENDING_RECOVERY", "MCU_ACCEPTED", null))
                .isEqualTo(Action.MARK_IN_PROGRESS);
    }

    @Test
    void leavesEdgeRestartForExistingAbortProjection() {
        assertThat(decide(
                "IN_PROGRESS", "FAILED", "EDGE_RESTARTED"))
                .isEqualTo(Action.NONE);
    }

    @Test
    void terminalSessionsNeverRegressOnLateEvidence() {
        for (String status : new String[]{
                "BUSINESS_CONFIRMED", "PRE_OPEN_ENDED", "DEVICE_ABORTED"}) {
            assertThat(decide(status, "MCU_ACCEPTED", null))
                    .isEqualTo(Action.NONE);
        }
    }

    @Test
    void rejectsUnknownObservationStages() {
        assertThatThrownBy(() -> decide(
                "AUTHORIZATION_QUEUED", "FUTURE_STAGE", null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    private static Action decide(
            String status,
            String stage,
            String errorCode) {
        return DeliveryCommandObservationDecision.decide(
                "START_DELIVERY_SESSION", status, stage, errorCode);
    }
}
