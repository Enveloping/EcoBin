package org.enveloping.ecobin.funds.application.pagination;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.util.Base64;
import java.util.HexFormat;
import java.util.Map;

@Component
public final class FundsListCursorCodec {

    private static final int VERSION = 1;
    private static final int MAX_ENCODED_LENGTH = 4_096;
    private static final Duration VALIDITY = Duration.ofHours(24);
    private static final byte[] DOMAIN =
            "ecobin:funds-list-cursor:v1:"
                    .getBytes(StandardCharsets.UTF_8);

    private final ObjectMapper objectMapper;
    private final byte[] signingKey;
    private final Clock clock;

    @Autowired
    public FundsListCursorCodec(
            ObjectMapper objectMapper,
            @Value("${jwt.secret}") String secret) {
        this(objectMapper, secret, Clock.systemUTC());
    }

    FundsListCursorCodec(
            ObjectMapper objectMapper,
            String secret,
            Clock clock) {
        this.objectMapper = objectMapper;
        if (secret == null
                || secret.getBytes(StandardCharsets.UTF_8).length < 32) {
            throw new IllegalArgumentException(
                    "funds cursor signing secret must be at least 32 bytes");
        }
        this.signingKey = secret.getBytes(StandardCharsets.UTF_8);
        this.clock = clock;
    }

    public String encode(
            String mode,
            String fingerprint,
            Instant asOf,
            long highWatermark,
            LocalDateTime lastOccurredAt,
            String lastStableKey,
            long lastId) {
        return encode(new CursorPayload(
                VERSION, mode, fingerprint, asOf, highWatermark,
                lastOccurredAt, lastStableKey, lastId,
                clock.instant().plus(VALIDITY).getEpochSecond()));
    }

    public Decoded decode(
            String cursor,
            String expectedMode,
            String expectedFingerprint) {
        try {
            if (cursor == null || cursor.isBlank()
                    || cursor.length() > MAX_ENCODED_LENGTH) {
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
                    || !expectedMode.equals(payload.mode())
                    || !MessageDigest.isEqual(
                    expectedFingerprint.getBytes(StandardCharsets.UTF_8),
                    payload.fingerprint().getBytes(StandardCharsets.UTF_8))
                    || payload.asOf() == null
                    || payload.highWatermark() < 0
                    || payload.lastOccurredAt() == null
                    || payload.lastStableKey() == null
                    || payload.lastStableKey().isBlank()
                    || payload.lastId() <= 0
                    || !Instant.ofEpochSecond(payload.expiresAtEpochSecond())
                    .isAfter(clock.instant())) {
                throw invalidCursor();
            }
            return new Decoded(
                    payload.asOf(), payload.highWatermark(),
                    payload.lastOccurredAt(), payload.lastStableKey(),
                    payload.lastId());
        } catch (TargetApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw invalidCursor();
        }
    }

    public static String fingerprint(Object... components) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            for (Object component : components) {
                byte[] value = String.valueOf(component)
                        .getBytes(StandardCharsets.UTF_8);
                digest.update(ByteBuffer.allocate(Integer.BYTES)
                        .putInt(value.length).array());
                digest.update(value);
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch (Exception exception) {
            throw new IllegalStateException("SHA-256 unavailable", exception);
        }
    }

    private String encode(CursorPayload payload) {
        try {
            String body = Base64.getUrlEncoder().withoutPadding()
                    .encodeToString(objectMapper.writeValueAsBytes(payload));
            String signature = Base64.getUrlEncoder().withoutPadding()
                    .encodeToString(sign(body));
            return body + "." + signature;
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "funds cursor cannot be encoded", exception);
        }
    }

    private byte[] sign(String body) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(signingKey, "HmacSHA256"));
            mac.update(DOMAIN);
            return mac.doFinal(body.getBytes(StandardCharsets.US_ASCII));
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "HmacSHA256 is unavailable", exception);
        }
    }

    private static TargetApiException invalidCursor() {
        return new TargetApiException(
                400, "COMMON.INVALID_CURSOR",
                "资金列表分页游标无效、已过期或与筛选条件不匹配",
                false, Map.of());
    }

    public record Decoded(
            Instant asOf,
            long highWatermark,
            LocalDateTime lastOccurredAt,
            String lastStableKey,
            long lastId) {
    }

    private record CursorPayload(
            int version,
            String mode,
            String fingerprint,
            Instant asOf,
            long highWatermark,
            LocalDateTime lastOccurredAt,
            String lastStableKey,
            long lastId,
            long expiresAtEpochSecond) {
    }
}
