package org.enveloping.ecobin.integration.onenet.inbound;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Target OneNet contract canonicalizer. It deliberately supports only the
 * stable JSON subset frozen by the machine contract.
 */
final class OneNetCanonicalJson {

    private static final long SAFE_INTEGER_MAX =
            9_007_199_254_740_991L;
    private static final byte[] EVENT_DOMAIN =
            "ECOBIN:ONENET:EVENT:v2\0"
                    .getBytes(StandardCharsets.UTF_8);

    private OneNetCanonicalJson() {
    }

    static String payloadSha256(Object payload) {
        return sha256Hex(canonicalBytes(payload));
    }

    static String eventCanonicalSha256(
            Map<String, Object> event,
            String trustedProductId,
            String trustedDeviceName) {
        Map<String, Object> source = new LinkedHashMap<>();
        source.put("productId", trustedProductId);
        source.put("deviceName", trustedDeviceName);
        Map<String, Object> projection = new LinkedHashMap<>();
        projection.put("trustedSource", source);
        for (String field : List.of(
                "schemaVersion",
                "eventUid",
                "edgeEventSequence",
                "eventType",
                "deliveryClass",
                "target",
                "commandUid",
                "occurredAt",
                "clockQuality",
                "payloadSha256")) {
            if (!event.containsKey(field)) {
                throw new IllegalArgumentException(
                        "event canonical projection misses " + field);
            }
            projection.put(field, event.get(field));
        }
        byte[] canonical = canonicalBytes(projection);
        byte[] preimage = new byte[EVENT_DOMAIN.length + canonical.length];
        System.arraycopy(
                EVENT_DOMAIN, 0, preimage, 0, EVENT_DOMAIN.length);
        System.arraycopy(
                canonical,
                0,
                preimage,
                EVENT_DOMAIN.length,
                canonical.length);
        return sha256Hex(preimage);
    }

    static String stablePrincipalKey(
            String productId, String deviceName) {
        return "onenet:"
                + sha256Hex(
                (productId + "\0" + deviceName)
                        .getBytes(StandardCharsets.UTF_8));
    }

    private static byte[] canonicalBytes(Object value) {
        StringBuilder output = new StringBuilder();
        append(output, value, "$");
        return output.toString().getBytes(StandardCharsets.UTF_8);
    }

    private static void append(
            StringBuilder output, Object value, String path) {
        if (value == null) {
            output.append("null");
            return;
        }
        if (value instanceof Boolean bool) {
            output.append(bool ? "true" : "false");
            return;
        }
        if (value instanceof Byte
                || value instanceof Short
                || value instanceof Integer
                || value instanceof Long) {
            long number = ((Number) value).longValue();
            if (number < -SAFE_INTEGER_MAX
                    || number > SAFE_INTEGER_MAX) {
                throw new IllegalArgumentException(
                        path + " contains an unsafe integer");
            }
            output.append(number);
            return;
        }
        if (value instanceof Number) {
            throw new IllegalArgumentException(
                    path + " contains floating point");
        }
        if (value instanceof String text) {
            appendString(output, text, path);
            return;
        }
        if (value instanceof List<?> list) {
            output.append('[');
            for (int index = 0; index < list.size(); index++) {
                if (index > 0) {
                    output.append(',');
                }
                append(output, list.get(index), path + "[" + index + "]");
            }
            output.append(']');
            return;
        }
        if (value instanceof Map<?, ?> map) {
            List<Map.Entry<String, Object>> entries = new ArrayList<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (!(entry.getKey() instanceof String)) {
                    throw new IllegalArgumentException(
                            path + " contains a non-text key");
                }
                @SuppressWarnings("unchecked")
                Map.Entry<String, Object> typed =
                        (Map.Entry<String, Object>)
                                (Map.Entry<?, ?>) entry;
                entries.add(typed);
            }
            entries.sort(Comparator.comparing(Map.Entry::getKey));
            output.append('{');
            for (int index = 0; index < entries.size(); index++) {
                if (index > 0) {
                    output.append(',');
                }
                Map.Entry<String, Object> entry = entries.get(index);
                appendString(output, entry.getKey(), path + ".<key>");
                output.append(':');
                append(
                        output,
                        entry.getValue(),
                        path + "." + entry.getKey());
            }
            output.append('}');
            return;
        }
        throw new IllegalArgumentException(
                path + " contains unsupported canonical content");
    }

    private static void appendString(
            StringBuilder output, String value, String path) {
        output.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (Character.isHighSurrogate(character)) {
                if (index + 1 >= value.length()
                        || !Character.isLowSurrogate(
                        value.charAt(index + 1))) {
                    throw new IllegalArgumentException(
                            path + " contains an unpaired surrogate");
                }
                output.append(character);
                output.append(value.charAt(++index));
                continue;
            }
            if (Character.isLowSurrogate(character)) {
                throw new IllegalArgumentException(
                        path + " contains an unpaired surrogate");
            }
            switch (character) {
                case '"' -> output.append("\\\"");
                case '\\' -> output.append("\\\\");
                case '\b' -> output.append("\\b");
                case '\t' -> output.append("\\t");
                case '\n' -> output.append("\\n");
                case '\f' -> output.append("\\f");
                case '\r' -> output.append("\\r");
                default -> {
                    if (character < 0x20) {
                        output.append(String.format(
                                "\\u%04x", (int) character));
                    } else {
                        output.append(character);
                    }
                }
            }
        }
        output.append('"');
    }

    private static String sha256Hex(byte[] value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value);
            return java.util.HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", exception);
        }
    }
}
