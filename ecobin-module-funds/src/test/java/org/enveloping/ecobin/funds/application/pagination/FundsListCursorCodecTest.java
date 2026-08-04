package org.enveloping.ecobin.funds.application.pagination;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class FundsListCursorCodecTest {

    private static final Instant NOW = Instant.parse("2026-08-04T00:00:00Z");
    private static final String SECRET =
            "funds-list-cursor-test-secret-is-32-bytes";

    private final FundsListCursorCodec codec = new FundsListCursorCodec(
            JsonMapper.builder().build(), SECRET,
            Clock.fixed(NOW, ZoneOffset.UTC));

    @Test
    void preservesSnapshotAndExclusiveAnchor() {
        LocalDateTime anchor = LocalDateTime.of(
                2026, 8, 3, 12, 34, 56, 123_000_000);
        String cursor = codec.encode(
                "RECHARGE", "scope-filter", NOW,
                91L, anchor, "RC0002", 87L);

        FundsListCursorCodec.Decoded decoded = codec.decode(
                cursor, "RECHARGE", "scope-filter");

        assertEquals(NOW, decoded.asOf());
        assertEquals(91L, decoded.highWatermark());
        assertEquals(anchor, decoded.lastOccurredAt());
        assertEquals("RC0002", decoded.lastStableKey());
        assertEquals(87L, decoded.lastId());
    }

    @Test
    void rejectsTamperingAndFilterReuse() {
        String cursor = codec.encode(
                "WITHDRAWAL", "filter-a", NOW,
                10L, LocalDateTime.of(2026, 8, 3, 0, 0),
                "WD0001", 9L);

        TargetApiException mismatch = assertThrows(
                TargetApiException.class,
                () -> codec.decode(cursor, "WITHDRAWAL", "filter-b"));
        assertEquals("COMMON.INVALID_CURSOR", mismatch.code());
        assertThrows(
                TargetApiException.class,
                () -> codec.decode(cursor + "x", "WITHDRAWAL", "filter-a"));
        assertThrows(
                TargetApiException.class,
                () -> codec.decode("a".repeat(4_097),
                        "WITHDRAWAL", "filter-a"));
    }
}
