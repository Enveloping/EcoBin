package org.enveloping.ecobin.funds.application.walletquery;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class WalletCursorCodecTest {

    private static final Instant NOW =
            Instant.parse("2026-07-30T12:00:00Z");
    private static final Clock FIXED_CLOCK =
            Clock.fixed(NOW, ZoneOffset.UTC);
    private static final String SECRET =
            "wallet-cursor-test-signing-secret-32-bytes";
    private static final String FINGERPRINT =
            "wallet-filter-fingerprint";
    private static final UUID USER_UID =
            UUID.fromString("11111111-1111-4111-8111-111111111111");
    private static final LocalDateTime OCCURRED_AT =
            LocalDateTime.of(2026, 7, 30, 19, 30, 0, 123_000_000);

    private final ObjectMapper objectMapper = JsonMapper.builder()
            .findAndAddModules()
            .build();
    private final WalletCursorCodec codec = new WalletCursorCodec(
            objectMapper,
            SECRET,
            FIXED_CLOCK);

    @Test
    void personalCursorPreservesExclusiveSequenceAnchor() {
        String cursor = codec.encodePersonal(
                NOW,
                17,
                FINGERPRINT);

        WalletCursorCodec.DecodedPersonal decoded =
                codec.decodePersonal(cursor, FINGERPRINT);

        assertThat(decoded.asOf()).isEqualTo(NOW);
        assertThat(decoded.lastSequence()).isEqualTo(17);
    }

    @Test
    void organizationCursorPreservesFrozenWatermarkAndTieBreakers() {
        String cursor = codec.encodeOrganization(
                NOW,
                91,
                OCCURRED_AT,
                USER_UID,
                17,
                FINGERPRINT);

        WalletCursorCodec.DecodedOrganization decoded =
                codec.decodeOrganization(cursor, FINGERPRINT);

        assertThat(decoded.asOf()).isEqualTo(NOW);
        assertThat(decoded.highWatermark()).isEqualTo(91);
        assertThat(decoded.lastOccurredAt()).isEqualTo(OCCURRED_AT);
        assertThat(decoded.lastOrganizationUserUid())
                .isEqualTo(USER_UID);
        assertThat(decoded.lastSequence()).isEqualTo(17);
    }

    @Test
    void rejectsTamperingChangedFiltersWrongModeAndExpiry() {
        String personal = codec.encodePersonal(
                NOW,
                17,
                FINGERPRINT);
        String tampered = personal.substring(0, personal.length() - 1)
                + (personal.endsWith("A") ? "B" : "A");

        assertInvalid(() ->
                codec.decodePersonal(tampered, FINGERPRINT));
        assertInvalid(() ->
                codec.decodePersonal(personal, "changed-filter"));
        assertInvalid(() ->
                codec.decodeOrganization(personal, FINGERPRINT));

        WalletCursorCodec expired = new WalletCursorCodec(
                objectMapper,
                SECRET,
                Clock.fixed(
                        NOW.plus(Duration.ofHours(24)).plusSeconds(1),
                        ZoneOffset.UTC));
        assertInvalid(() ->
                expired.decodePersonal(personal, FINGERPRINT));
    }

    private static void assertInvalid(Runnable action) {
        assertThatThrownBy(action::run)
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status()).isEqualTo(400);
                            assertThat(failure.code())
                                    .isEqualTo("COMMON.INVALID_CURSOR");
                            assertThat(failure.retryable()).isFalse();
                            assertThat(failure.details()).isEmpty();
                        });
    }
}
