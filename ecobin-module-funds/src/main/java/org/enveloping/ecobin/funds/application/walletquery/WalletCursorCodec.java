package org.enveloping.ecobin.funds.application.walletquery;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.util.Base64;
import java.util.Map;
import java.util.UUID;

@Component
final class WalletCursorCodec {

    private static final int VERSION = 1;
    private static final Duration VALIDITY = Duration.ofHours(24);
    private static final byte[] DOMAIN =
            "ecobin:wallet-entry-cursor:v1:"
                    .getBytes(StandardCharsets.UTF_8);

    private final ObjectMapper objectMapper;
    private final byte[] signingKey;
    private final Clock clock;

    @Autowired
    WalletCursorCodec(
            ObjectMapper objectMapper,
            @Value("${jwt.secret}") String secret) {
        this(objectMapper, secret, Clock.systemUTC());
    }

    WalletCursorCodec(
            ObjectMapper objectMapper,
            String secret,
            Clock clock) {
        this.objectMapper = objectMapper;
        if (secret == null
                || secret.getBytes(StandardCharsets.UTF_8).length < 32) {
            throw new IllegalArgumentException(
                    "wallet cursor signing secret must be at least 32 bytes");
        }
        this.signingKey = secret.getBytes(StandardCharsets.UTF_8);
        this.clock = clock;
    }

    String encodePersonal(
            Instant asOf,
            long lastSequence,
            String fingerprint) {
        return encode(new CursorPayload(
                VERSION,
                "PERSONAL",
                fingerprint,
                asOf,
                0,
                lastSequence,
                null,
                null,
                expiresAt()));
    }

    String encodeOrganization(
            Instant asOf,
            long highWatermark,
            LocalDateTime lastOccurredAt,
            UUID lastOrganizationUserUid,
            long lastSequence,
            String fingerprint) {
        return encode(new CursorPayload(
                VERSION,
                "ORGANIZATION",
                fingerprint,
                asOf,
                highWatermark,
                lastSequence,
                lastOccurredAt,
                lastOrganizationUserUid,
                expiresAt()));
    }

    DecodedPersonal decodePersonal(
            String cursor,
            String fingerprint) {
        CursorPayload payload = decode(
                cursor,
                "PERSONAL",
                fingerprint);
        if (payload.asOf() == null
                || payload.lastSequence() < 1
                || payload.highWatermark() != 0
                || payload.lastOccurredAt() != null
                || payload.lastOrganizationUserUid() != null) {
            throw invalidCursor();
        }
        return new DecodedPersonal(
                payload.asOf(),
                payload.lastSequence());
    }

    DecodedOrganization decodeOrganization(
            String cursor,
            String fingerprint) {
        CursorPayload payload = decode(
                cursor,
                "ORGANIZATION",
                fingerprint);
        if (payload.asOf() == null
                || payload.highWatermark() < 0
                || payload.lastSequence() < 1
                || payload.lastOccurredAt() == null
                || payload.lastOrganizationUserUid() == null) {
            throw invalidCursor();
        }
        return new DecodedOrganization(
                payload.asOf(),
                payload.highWatermark(),
                payload.lastOccurredAt(),
                payload.lastOrganizationUserUid(),
                payload.lastSequence());
    }

    private String encode(CursorPayload payload) {
        try {
            String body = Base64.getUrlEncoder()
                    .withoutPadding()
                    .encodeToString(
                            objectMapper.writeValueAsBytes(payload));
            String signature = Base64.getUrlEncoder()
                    .withoutPadding()
                    .encodeToString(sign(body));
            return body + "." + signature;
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "wallet cursor cannot be encoded",
                    exception);
        }
    }

    private CursorPayload decode(
            String cursor,
            String mode,
            String fingerprint) {
        try {
            if (cursor == null || cursor.isBlank()) {
                throw invalidCursor();
            }
            String[] segments = cursor.split("\\.", -1);
            if (segments.length != 2
                    || segments[0].isBlank()
                    || segments[1].isBlank()
                    || !MessageDigest.isEqual(
                    sign(segments[0]),
                    Base64.getUrlDecoder().decode(segments[1]))) {
                throw invalidCursor();
            }
            CursorPayload payload = objectMapper.readValue(
                    Base64.getUrlDecoder().decode(segments[0]),
                    CursorPayload.class);
            if (payload.version() != VERSION
                    || !mode.equals(payload.mode())
                    || !MessageDigest.isEqual(
                    fingerprint.getBytes(StandardCharsets.UTF_8),
                    payload.fingerprint()
                            .getBytes(StandardCharsets.UTF_8))
                    || Instant.ofEpochSecond(
                                    payload.expiresAtEpochSecond())
                            .isBefore(clock.instant())) {
                throw invalidCursor();
            }
            return payload;
        } catch (TargetApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw invalidCursor();
        }
    }

    private long expiresAt() {
        return clock.instant().plus(VALIDITY).getEpochSecond();
    }

    private byte[] sign(String body) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(signingKey, "HmacSHA256"));
            mac.update(DOMAIN);
            return mac.doFinal(
                    body.getBytes(StandardCharsets.US_ASCII));
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "HmacSHA256 is unavailable",
                    exception);
        }
    }

    private static TargetApiException invalidCursor() {
        return new TargetApiException(
                400,
                "COMMON.INVALID_CURSOR",
                "钱包流水分页游标无效或已过期",
                false,
                Map.of());
    }

    record DecodedPersonal(Instant asOf, long lastSequence) {
    }

    record DecodedOrganization(
            Instant asOf,
            long highWatermark,
            LocalDateTime lastOccurredAt,
            UUID lastOrganizationUserUid,
            long lastSequence) {
    }

    private record CursorPayload(
            int version,
            String mode,
            String fingerprint,
            Instant asOf,
            long highWatermark,
            long lastSequence,
            LocalDateTime lastOccurredAt,
            UUID lastOrganizationUserUid,
            long expiresAtEpochSecond) {
    }
}
