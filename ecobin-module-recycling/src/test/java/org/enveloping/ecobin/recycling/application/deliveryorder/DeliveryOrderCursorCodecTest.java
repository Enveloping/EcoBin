package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class DeliveryOrderCursorCodecTest {

    private static final Instant NOW =
            Instant.parse("2026-07-29T12:00:00Z");
    private static final Clock FIXED_CLOCK =
            Clock.fixed(NOW, ZoneOffset.UTC);
    private static final String SECRET =
            "cursor-test-signing-secret-32-bytes-long";
    private static final String FILTER_FINGERPRINT =
            "tenant=7&status=PENDING&device=all";
    private static final LocalDateTime SORT_TIME =
            LocalDateTime.of(2026, 7, 29, 19, 30, 0, 123_000_000);

    private final ObjectMapper objectMapper = JsonMapper.builder()
            .findAndAddModules()
            .build();
    private final DeliveryOrderCursorCodec codec =
            new DeliveryOrderCursorCodec(
                    objectMapper,
                    SECRET,
                    FIXED_CLOCK);

    @Test
    void encodesAndDecodesAllPagingKeys() {
        String cursor = codec.encode(
                912L,
                SORT_TIME,
                "DO2026072900912",
                FILTER_FINGERPRINT);

        DeliveryOrderCursorCodec.DecodedCursor decoded =
                codec.decode(cursor, FILTER_FINGERPRINT);

        assertThat(decoded.highWatermark()).isEqualTo(912L);
        assertThat(decoded.lastSortTime()).isEqualTo(SORT_TIME);
        assertThat(decoded.lastOrderNo())
                .isEqualTo("DO2026072900912");
        assertThat(cursor.split("\\.", -1)).hasSize(2);
    }

    @Test
    void rejectsTamperedSignature() {
        String cursor = codec.encode(
                912L,
                SORT_TIME,
                "DO2026072900912",
                FILTER_FINGERPRINT);
        String[] segments = cursor.split("\\.", -1);
        String signature = segments[1];
        char replacement = signature.charAt(0) == 'A' ? 'B' : 'A';
        String tampered = segments[0]
                + "."
                + replacement
                + signature.substring(1);

        assertInvalidCursor(tampered, FILTER_FINGERPRINT);
    }

    @Test
    void rejectsCursorWhenFilterFingerprintChanges() {
        String cursor = codec.encode(
                912L,
                SORT_TIME,
                "DO2026072900912",
                FILTER_FINGERPRINT);

        assertInvalidCursor(
                cursor,
                "tenant=7&status=APPROVED&device=all");
    }

    @Test
    void rejectsExpiredCursor() {
        String cursor = codec.encode(
                912L,
                SORT_TIME,
                "DO2026072900912",
                FILTER_FINGERPRINT);
        Clock afterExpiry = Clock.fixed(
                NOW.plus(Duration.ofHours(24)).plusSeconds(1),
                ZoneOffset.UTC);
        DeliveryOrderCursorCodec expiredCodec =
                new DeliveryOrderCursorCodec(
                        objectMapper,
                        SECRET,
                        afterExpiry);

        assertInvalidCursor(
                expiredCodec,
                cursor,
                FILTER_FINGERPRINT);
    }

    @Test
    void rejectsStructurallyMalformedCursor() {
        assertInvalidCursor(null, FILTER_FINGERPRINT);
        assertInvalidCursor("", FILTER_FINGERPRINT);
        assertInvalidCursor(" ", FILTER_FINGERPRINT);
        assertInvalidCursor("one-segment", FILTER_FINGERPRINT);
        assertInvalidCursor("a.b.c", FILTER_FINGERPRINT);
        assertInvalidCursor(".", FILTER_FINGERPRINT);
        assertInvalidCursor("***.***", FILTER_FINGERPRINT);
    }

    @Test
    void preservesOrderNumberAsTieBreakerForSameSortTime() {
        String firstCursor = codec.encode(
                912L,
                SORT_TIME,
                "DO2026072900100",
                FILTER_FINGERPRINT);
        String secondCursor = codec.encode(
                912L,
                SORT_TIME,
                "DO2026072900101",
                FILTER_FINGERPRINT);

        DeliveryOrderCursorCodec.DecodedCursor first =
                codec.decode(firstCursor, FILTER_FINGERPRINT);
        DeliveryOrderCursorCodec.DecodedCursor second =
                codec.decode(secondCursor, FILTER_FINGERPRINT);

        assertThat(first.lastSortTime()).isEqualTo(second.lastSortTime());
        assertThat(first.lastOrderNo())
                .isEqualTo("DO2026072900100");
        assertThat(second.lastOrderNo())
                .isEqualTo("DO2026072900101");
        assertThat(first.lastOrderNo())
                .isNotEqualTo(second.lastOrderNo());
    }

    @Test
    void codecAndInvalidCursorErrorDoNotLeakSecretOrCursorPayload() {
        String cursor = codec.encode(
                912L,
                SORT_TIME,
                "DO2026072900912",
                FILTER_FINGERPRINT);
        String body = cursor.substring(0, cursor.indexOf('.'));
        String tampered = cursor.substring(0, cursor.length() - 1)
                + (cursor.endsWith("A") ? "B" : "A");

        assertThat(codec.toString())
                .doesNotContain(SECRET)
                .doesNotContain(cursor)
                .doesNotContain(body);

        assertThatThrownBy(() ->
                codec.decode(tampered, FILTER_FINGERPRINT))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.getMessage())
                                    .doesNotContain(SECRET)
                                    .doesNotContain(cursor)
                                    .doesNotContain(body)
                                    .doesNotContain(
                                            "DO2026072900912")
                                    .doesNotContain(
                                            FILTER_FINGERPRINT);
                            assertThat(failure.toString())
                                    .doesNotContain(SECRET)
                                    .doesNotContain(cursor)
                                    .doesNotContain(body);
                            assertThat(failure.details()).isEmpty();
                            assertThat(failure.getCause()).isNull();
                        });
    }

    private void assertInvalidCursor(
            String cursor,
            String expectedFilterFingerprint) {
        assertInvalidCursor(
                codec,
                cursor,
                expectedFilterFingerprint);
    }

    private static void assertInvalidCursor(
            DeliveryOrderCursorCodec targetCodec,
            String cursor,
            String expectedFilterFingerprint) {
        assertThatThrownBy(() ->
                targetCodec.decode(
                        cursor,
                        expectedFilterFingerprint))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status()).isEqualTo(400);
                            assertThat(failure.code())
                                    .isEqualTo(
                                            "COMMON.INVALID_CURSOR");
                            assertThat(failure.retryable()).isFalse();
                            assertThat(failure.details()).isEmpty();
                        });
    }
}
