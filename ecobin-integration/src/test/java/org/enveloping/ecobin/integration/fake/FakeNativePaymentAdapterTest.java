package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort;
import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;

class FakeNativePaymentAdapterTest {

    @Test
    void closeIsVisibleToTheRequiredFinalQuery() {
        FakeNativePaymentAdapter adapter = new FakeNativePaymentAdapter(false);
        adapter.create(new NativePaymentChannelPort.NativePaymentRequest(
                "FAKE1900000001",
                "wx1234567890abcdef",
                "NP1234567890ABCDEF1234567890ABCD",
                100,
                "test",
                Instant.parse("2026-08-03T10:30:00Z"),
                "https://fake.invalid/notify"));
        var query = new NativePaymentChannelPort.NativePaymentQuery(
                "FAKE1900000001", "NP1234567890ABCDEF1234567890ABCD");

        assertEquals(
                NativePaymentChannelPort.NativePaymentResult.Outcome.CLOSED,
                adapter.close(query).outcome());
        assertEquals(
                NativePaymentChannelPort.NativePaymentResult.Outcome.CLOSED,
                adapter.query(query).outcome());
    }
}
