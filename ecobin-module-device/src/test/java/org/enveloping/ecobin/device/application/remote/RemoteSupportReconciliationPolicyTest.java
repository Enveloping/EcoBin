package org.enveloping.ecobin.device.application.remote;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class RemoteSupportReconciliationPolicyTest {

    @Test
    void openSessionWaitsForReconnectWhenActualTemporarilyDisappears() {
        var decision = RemoteSupportReconciliationPolicy.decide(
                "OPEN",
                RemoteSupportReconciliationPolicy.ActualLeaseState.ABSENT);

        assertThat(decision.nextState()).isEqualTo("RECONNECTING");
        assertThat(decision.desiredRequired()).isTrue();
        assertThat(decision.releaseLease()).isFalse();
        assertThat(decision.failureCode()).isNull();
    }

    @Test
    void matchingActualRestoresAReconnectingSession() {
        var decision = RemoteSupportReconciliationPolicy.decide(
                "RECONNECTING",
                RemoteSupportReconciliationPolicy.ActualLeaseState.MATCH);

        assertThat(decision.nextState()).isEqualTo("OPEN");
        assertThat(decision.desiredRequired()).isTrue();
        assertThat(decision.releaseLease()).isFalse();
    }

    @Test
    void offlineCloseFinishesAsSoonAsActualIsAbsent() {
        var decision = RemoteSupportReconciliationPolicy.decide(
                "CLOSING",
                RemoteSupportReconciliationPolicy.ActualLeaseState.ABSENT);

        assertThat(decision.nextState()).isEqualTo("CLOSED");
        assertThat(decision.desiredRequired()).isFalse();
        assertThat(decision.releaseLease()).isTrue();
    }

    @Test
    void conflictingActualFailsClosedWithoutReusingThePort() {
        var decision = RemoteSupportReconciliationPolicy.decide(
                "OPEN",
                RemoteSupportReconciliationPolicy.ActualLeaseState.CONFLICT);

        assertThat(decision.nextState()).isEqualTo("FAILED");
        assertThat(decision.desiredRequired()).isFalse();
        assertThat(decision.releaseLease()).isFalse();
        assertThat(decision.failureCode())
                .isEqualTo("SERVER_LEASE_CONFLICT");
    }
}
