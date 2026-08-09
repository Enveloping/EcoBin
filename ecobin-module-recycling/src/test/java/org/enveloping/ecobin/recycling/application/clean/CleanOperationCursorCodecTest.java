package org.enveloping.ecobin.recycling.application.clean;

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

class CleanOperationCursorCodecTest {

    private static final Instant NOW =
            Instant.parse("2026-08-09T00:00:00Z");
    private static final Clock CLOCK = Clock.fixed(NOW, ZoneOffset.UTC);
    private static final String SECRET =
            "clean-operation-test-secret-32-bytes";
    private static final String FILTER = "scope-and-filter-fingerprint";
    private static final LocalDateTime CREATED_AT =
            LocalDateTime.of(2026, 8, 8, 17, 30, 0, 123_000_000);
    private final ObjectMapper mapper = JsonMapper.builder()
            .findAndAddModules()
            .build();
    private final CleanOperationCursorCodec codec =
            new CleanOperationCursorCodec(mapper, SECRET, CLOCK);

    @Test
    void roundTripsSnapshotAndStableSortKeys() {
        String cursor = codec.encode(912L, CREATED_AT, 811L, FILTER);

        var decoded = codec.decode(cursor, FILTER);

        assertThat(decoded.highWatermark()).isEqualTo(912L);
        assertThat(decoded.lastCreatedAt()).isEqualTo(CREATED_AT);
        assertThat(decoded.lastId()).isEqualTo(811L);
    }

    @Test
    void rejectsTamperingFilterChangesAndExpiry() {
        String cursor = codec.encode(912L, CREATED_AT, 811L, FILTER);
        String tampered = cursor.substring(0, cursor.length() - 1)
                + (cursor.endsWith("A") ? "B" : "A");

        assertInvalid(codec, tampered, FILTER);
        assertInvalid(codec, cursor, "different-filter");
        assertInvalid(
                new CleanOperationCursorCodec(
                        mapper,
                        SECRET,
                        Clock.fixed(
                                NOW.plus(Duration.ofHours(24))
                                        .plusSeconds(1),
                                ZoneOffset.UTC)),
                cursor,
                FILTER);
    }

    private static void assertInvalid(
            CleanOperationCursorCodec target,
            String cursor,
            String filter) {
        assertThatThrownBy(() -> target.decode(cursor, filter))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status()).isEqualTo(400);
                            assertThat(failure.code())
                                    .isEqualTo("COMMON.INVALID_CURSOR");
                            assertThat(failure.details()).isEmpty();
                        });
    }
}
