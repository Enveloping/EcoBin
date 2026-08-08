package org.enveloping.ecobin.recycling.application.bag;

import org.enveloping.ecobin.device.api.port.BagCodeAdmissionPort;
import org.enveloping.ecobin.recycling.infrastructure.bag.BagCodeAuthenticationProperties;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Issues and authenticates canonical EB1 physical bag labels. */
@Service
public final class Eb1BagCodeService implements BagCodeAdmissionPort {

    private static final Pattern CODE = Pattern.compile(
            "^EB1_(K[0-9A-Z]{1,6})_"
                    + "([0-9A-HJKMNP-TV-Z]{26})_"
                    + "([0-9A-HJKMNP-TV-Z]{20})$");
    private static final byte[] DOMAIN =
            "ecobin:bag:EB1:".getBytes(StandardCharsets.US_ASCII);
    private static final char[] CROCKFORD =
            "0123456789ABCDEFGHJKMNPQRSTVWXYZ".toCharArray();
    private static final SecureRandom RANDOM = new SecureRandom();

    private final String activeKeyId;
    private final Map<String, byte[]> keys;

    public Eb1BagCodeService(BagCodeAuthenticationProperties properties) {
        activeKeyId = normalizeKeyId(properties.getActiveKeyId());
        LinkedHashMap<String, byte[]> decoded = new LinkedHashMap<>();
        properties.getKeys().forEach((id, encoded) ->
                decoded.put(normalizeKeyId(id), decodeKey(encoded)));
        if (!decoded.containsKey(activeKeyId)) {
            throw new IllegalStateException(
                    "active bag-code HMAC key is not configured");
        }
        keys = Map.copyOf(decoded);
    }

    public IssuedBagCode issue() {
        byte[] serial = new byte[16];
        RANDOM.nextBytes(serial);
        return issue(activeKeyId, serial, keys.get(activeKeyId));
    }

    @Override
    public Optional<AuthenticatedBagCode> authenticate(String rawCode) {
        if (rawCode == null) {
            return Optional.empty();
        }
        String normalized = rawCode;
        Matcher matcher = CODE.matcher(normalized);
        if (!matcher.matches()) {
            return Optional.empty();
        }
        String keyId = matcher.group(1);
        byte[] key = keys.get(keyId);
        if (key == null) {
            return Optional.empty();
        }
        byte[] expected = authenticationTag(
                keyId, matcher.group(2), key)
                .getBytes(StandardCharsets.US_ASCII);
        byte[] actual = matcher.group(3)
                .getBytes(StandardCharsets.US_ASCII);
        if (!MessageDigest.isEqual(expected, actual)) {
            return Optional.empty();
        }
        return Optional.of(new AuthenticatedBagCode(normalized, keyId));
    }

    public String activeKeyId() {
        return activeKeyId;
    }

    static IssuedBagCode issue(
            String keyId,
            byte[] serialBytes,
            byte[] key) {
        String normalizedKeyId = normalizeKeyId(keyId);
        if (serialBytes == null || serialBytes.length != 16) {
            throw new IllegalArgumentException(
                    "EB1 serial must contain exactly 16 bytes");
        }
        String serial = crockford(serialBytes);
        String value = "EB1_" + normalizedKeyId + "_" + serial + "_"
                + authenticationTag(normalizedKeyId, serial, key);
        return new IssuedBagCode(value, normalizedKeyId);
    }

    static String authenticationTag(
            String keyId,
            String serial,
            byte[] key) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            mac.update(DOMAIN);
            mac.update(normalizeKeyId(keyId)
                    .getBytes(StandardCharsets.US_ASCII));
            mac.update((byte) ':');
            byte[] digest = mac.doFinal(
                    serial.getBytes(StandardCharsets.US_ASCII));
            return crockford(Arrays.copyOf(digest, 12));
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "HmacSHA256 is unavailable", exception);
        }
    }

    private static String crockford(byte[] value) {
        StringBuilder result = new StringBuilder(
                (value.length * 8 + 4) / 5);
        int buffer = 0;
        int bits = 0;
        for (byte item : value) {
            buffer = (buffer << 8) | (item & 0xff);
            bits += 8;
            while (bits >= 5) {
                bits -= 5;
                result.append(CROCKFORD[(buffer >> bits) & 31]);
            }
        }
        if (bits > 0) {
            result.append(CROCKFORD[(buffer << (5 - bits)) & 31]);
        }
        return result.toString();
    }

    private static byte[] decodeKey(String encoded) {
        try {
            if (encoded == null || encoded.isBlank()) {
                throw new IllegalArgumentException("blank key");
            }
            byte[] decoded = Base64.getDecoder().decode(encoded.trim());
            if (decoded.length < 32) {
                throw new IllegalArgumentException("short key");
            }
            return decoded;
        } catch (IllegalArgumentException exception) {
            throw new IllegalStateException(
                    "bag-code HMAC keys must be base64-encoded and contain at least 32 bytes",
                    exception);
        }
    }

    private static String normalizeKeyId(String value) {
        if (value == null || !value.matches("K[0-9A-Z]{1,6}")) {
            throw new IllegalStateException(
                    "bag-code key id must match K[0-9A-Z]{1,6}");
        }
        return value;
    }

    public record IssuedBagCode(String value, String keyId) {
    }
}
