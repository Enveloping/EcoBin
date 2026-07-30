package org.enveloping.ecobin.recycling.application.deliveryorder;

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

@Component
final class DeliveryOrderCursorCodec {

    private static final int VERSION = 1;
    private static final Duration VALIDITY = Duration.ofHours(24);
    private static final byte[] DOMAIN =
            "ecobin:delivery-order-cursor:v1:"
                    .getBytes(StandardCharsets.UTF_8);

    private final ObjectMapper objectMapper;
    private final byte[] signingKey;
    private final Clock clock;

    @Autowired
    DeliveryOrderCursorCodec(
            ObjectMapper objectMapper,
            @Value("${jwt.secret}") String secret) {
        this(objectMapper, secret, Clock.systemUTC());
    }

    DeliveryOrderCursorCodec(
            ObjectMapper objectMapper,
            String secret,
            Clock clock) {
        this.objectMapper = objectMapper;
        if (secret == null
                || secret.getBytes(StandardCharsets.UTF_8).length < 32) {
            throw new IllegalArgumentException(
                    "delivery cursor signing secret must be at least 32 bytes");
        }
        this.signingKey = secret.getBytes(StandardCharsets.UTF_8);
        this.clock = clock;
    }

    String encode(
            long highWatermark,
            LocalDateTime lastSortTime,
            String lastOrderNo,
            String filterFingerprint) {
        try {
            CursorPayload payload = new CursorPayload(
                    VERSION,
                    highWatermark,
                    lastSortTime,
                    lastOrderNo,
                    filterFingerprint,
                    clock.instant().plus(VALIDITY).getEpochSecond());
            String body = Base64.getUrlEncoder()
                    .withoutPadding()
                    .encodeToString(
                            objectMapper.writeValueAsBytes(payload));
            String signature = Base64.getUrlEncoder()
                    .withoutPadding()
                    .encodeToString(sign(body));
            return body + "." + signature;
        } catch (TargetApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "delivery order cursor cannot be encoded",
                    exception);
        }
    }

    DecodedCursor decode(
            String cursor,
            String expectedFilterFingerprint) {
        try {
            if (cursor == null || cursor.isBlank()) {
                throw invalidCursor();
            }
            String[] segments = cursor.split("\\.", -1);
            if (segments.length != 2
                    || segments[0].isBlank()
                    || segments[1].isBlank()) {
                throw invalidCursor();
            }
            byte[] actualSignature =
                    Base64.getUrlDecoder().decode(segments[1]);
            if (!MessageDigest.isEqual(
                    sign(segments[0]),
                    actualSignature)) {
                throw invalidCursor();
            }
            CursorPayload payload = objectMapper.readValue(
                    Base64.getUrlDecoder().decode(segments[0]),
                    CursorPayload.class);
            if (payload.version() != VERSION
                    || payload.highWatermark() < 0
                    || payload.lastSortTime() == null
                    || payload.lastOrderNo() == null
                    || payload.lastOrderNo().isBlank()
                    || !MessageDigest.isEqual(
                    payload.filterFingerprint()
                            .getBytes(StandardCharsets.UTF_8),
                    expectedFilterFingerprint
                            .getBytes(StandardCharsets.UTF_8))
                    || Instant.ofEpochSecond(payload.expiresAtEpochSecond())
                    .isBefore(clock.instant())) {
                throw invalidCursor();
            }
            return new DecodedCursor(
                    payload.highWatermark(),
                    payload.lastSortTime(),
                    payload.lastOrderNo());
        } catch (TargetApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw invalidCursor();
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
                    "HmacSHA256 is unavailable",
                    exception);
        }
    }

    private static TargetApiException invalidCursor() {
        return new TargetApiException(
                400,
                "COMMON.INVALID_CURSOR",
                "投递订单分页游标无效或已过期",
                false,
                Map.of());
    }

    record DecodedCursor(
            long highWatermark,
            LocalDateTime lastSortTime,
            String lastOrderNo) {
    }

    private record CursorPayload(
            int version,
            long highWatermark,
            LocalDateTime lastSortTime,
            String lastOrderNo,
            String filterFingerprint,
            long expiresAtEpochSecond) {
    }
}
