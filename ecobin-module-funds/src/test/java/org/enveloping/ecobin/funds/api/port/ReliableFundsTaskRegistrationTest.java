package org.enveloping.ecobin.funds.api.port;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;

class ReliableFundsTaskRegistrationTest {

    @Test
    void normalizesOnlyTheReliableTaskKeyToDatabaseAlphabet() {
        var registration = new ReliableFundsTaskRegistrationPort
                .ReliableFundsTaskRegistration(
                1,
                2,
                "CREATE_NATIVE_PAYMENT",
                "CREATE_NATIVE_PAYMENT:RCabcdef0123456789",
                "RECHARGE_ORDER",
                "RCabcdef0123456789",
                1,
                "{}",
                new byte[32],
                20,
                LocalDateTime.of(2026, 8, 3, 0, 0));

        assertEquals(
                "CREATE_NATIVE_PAYMENT:RCABCDEF0123456789",
                registration.taskKey());
        assertEquals("RCabcdef0123456789", registration.targetStableKey());
    }
}
