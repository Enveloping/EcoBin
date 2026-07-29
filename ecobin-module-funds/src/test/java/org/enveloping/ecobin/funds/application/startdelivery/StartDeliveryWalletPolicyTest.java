package org.enveloping.ecobin.funds.application.startdelivery;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class StartDeliveryWalletPolicyTest {

    @Test
    void openWalletStrictlyAboveFloorIsEligible() {
        assertDoesNotThrow(() ->
                StartDeliveryWalletPolicy.requireEligible(
                        -99,
                        "OPEN",
                        -100));
    }

    @Test
    void balanceEqualToFloorIsRejected() {
        assertDeliveryLimit(() ->
                StartDeliveryWalletPolicy.requireEligible(
                        -100,
                        "OPEN",
                        -100));
    }

    @Test
    void balanceBelowFloorIsRejected() {
        assertDeliveryLimit(() ->
                StartDeliveryWalletPolicy.requireEligible(
                        -101,
                        "OPEN",
                        -100));
    }

    @Test
    void latchedGateIsRejectedEvenAfterBalanceRecovers() {
        assertDeliveryLimit(() ->
                StartDeliveryWalletPolicy.requireEligible(
                        10_000,
                        "MANUAL_RECOVERY_REQUIRED",
                        -100));
    }

    @Test
    void unknownGateStateFailsClosed() {
        assertDeliveryLimit(() ->
                StartDeliveryWalletPolicy.requireEligible(
                        10_000,
                        "UNKNOWN",
                        -100));
    }

    private static void assertDeliveryLimit(Runnable action) {
        TargetApiException failure = assertThrows(
                TargetApiException.class,
                action::run);
        assertEquals(422, failure.status());
        assertEquals("WALLET.DELIVERY_LIMIT_REACHED", failure.code());
    }
}
