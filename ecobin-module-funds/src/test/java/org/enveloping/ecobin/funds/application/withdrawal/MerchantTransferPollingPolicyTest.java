package org.enveloping.ecobin.funds.application.withdrawal;

import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MerchantTransferPollingPolicyTest {

    private static final LocalDateTime BOUNDARY =
            LocalDateTime.of(2026, 8, 3, 10, 0);

    @Test
    void processingUsesTieredDelaysInsteadOfOneSecondHotPolling() {
        assertEquals(Duration.ofSeconds(30),
                MerchantTransferPollingPolicy.nextDelay(
                        BOUNDARY, BOUNDARY.plusMinutes(1)));
        assertEquals(Duration.ofMinutes(2),
                MerchantTransferPollingPolicy.nextDelay(
                        BOUNDARY, BOUNDARY.plusMinutes(10)));
        assertEquals(Duration.ofMinutes(10),
                MerchantTransferPollingPolicy.nextDelay(
                        BOUNDARY, BOUNDARY.plusHours(2)));
        assertEquals(Duration.ofHours(1),
                MerchantTransferPollingPolicy.nextDelay(
                        BOUNDARY, BOUNDARY.plusDays(2)));
        assertEquals(Duration.ofHours(6),
                MerchantTransferPollingPolicy.nextDelay(
                        BOUNDARY, BOUNDARY.plusDays(10)));
    }

    @Test
    void longUnsettledAndWechatQueryWindowHaveExplicitBoundaries() {
        assertFalse(MerchantTransferPollingPolicy.isLongUnsettled(
                BOUNDARY, BOUNDARY.plusMinutes(29)));
        assertTrue(MerchantTransferPollingPolicy.isLongUnsettled(
                BOUNDARY, BOUNDARY.plusMinutes(30)));
        assertFalse(MerchantTransferPollingPolicy.isQueryWindowExpired(
                BOUNDARY, BOUNDARY.plusDays(29)));
        assertTrue(MerchantTransferPollingPolicy.isQueryWindowExpired(
                BOUNDARY, BOUNDARY.plusDays(30)));
    }
}
