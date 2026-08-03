package org.enveloping.ecobin.funds.application.recharge;

import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort.NativePaymentResult;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class NativePaymentLifecyclePolicyTest {

    @Test
    void lateNotPayCannotSupersedeEstablishedSuccess() {
        assertTrue(NativePaymentLifecyclePolicy.successAlreadyEstablished(
                "PAID_PENDING_POST", "SUCCESS", "4200000001"));
        assertTrue(NativePaymentLifecyclePolicy.successAlreadyEstablished(
                "POSTED", "NOTPAY", null));
        assertFalse(NativePaymentLifecyclePolicy.successAlreadyEstablished(
                "PENDING_PAYMENT", "NOTPAY", null));
    }

    @Test
    void expirationOnlyClosesAnAuthoritativeNotPayObservation() {
        LocalDateTime expires = LocalDateTime.of(2026, 8, 3, 10, 0);
        LocalDateTime after = expires.plusSeconds(1);
        assertTrue(NativePaymentLifecyclePolicy
                .shouldCloseAfterExpiredUnpaidQuery(
                        expires, after, result("NOTPAY")));
        assertFalse(NativePaymentLifecyclePolicy
                .shouldCloseAfterExpiredUnpaidQuery(
                        expires, after, result("USERPAYING")));
        assertFalse(NativePaymentLifecyclePolicy
                .shouldCloseAfterExpiredUnpaidQuery(
                        expires, expires.minusSeconds(1), result("NOTPAY")));
        assertEquals("EXPIRED",
                NativePaymentLifecyclePolicy.closedBusinessState(expires, after));
    }

    private static NativePaymentResult result(String state) {
        return new NativePaymentResult(
                NativePaymentResult.Outcome.ACCEPTED,
                state,
                null,
                null,
                null,
                null,
                Instant.parse("2026-08-03T10:00:01Z"));
    }
}
