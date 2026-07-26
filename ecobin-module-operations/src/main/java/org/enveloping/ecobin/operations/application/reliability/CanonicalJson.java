package org.enveloping.ecobin.operations.application.reliability;

import org.springframework.stereotype.Component;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.ObjectReader;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;

@Component
public class CanonicalJson {

    private final ObjectMapper objectMapper;
    private final ObjectReader exactNumberReader;

    public CanonicalJson(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
        this.exactNumberReader = objectMapper.reader().with(
                DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS,
                DeserializationFeature.USE_BIG_INTEGER_FOR_INTS);
    }

    public CanonicalPayload canonicalize(
            String messageKind, int schemaVersion, String payload) {
        JsonNode root = exactNumberReader.readTree(payload);
        if (!root.isObject()) {
            throw new IllegalArgumentException(
                    "normalized payload must be a JSON object");
        }
        StringBuilder canonical = new StringBuilder(payload.length());
        appendCanonical(root, canonical);
        byte[] canonicalBytes = canonical.toString()
                .getBytes(StandardCharsets.UTF_8);
        byte[] semanticDigest = sha256LengthPrefixed(
                messageKind.getBytes(StandardCharsets.US_ASCII),
                ByteBuffer.allocate(Integer.BYTES).putInt(schemaVersion).array(),
                canonicalBytes);
        return new CanonicalPayload(
                canonical.toString(), semanticDigest, hex(semanticDigest));
    }

    public byte[] sha256(byte[] value) {
        return newDigest().digest(value);
    }

    public byte[] sha256LengthPrefixed(byte[]... values) {
        MessageDigest digest = newDigest();
        for (byte[] value : values) {
            digest.update(ByteBuffer.allocate(Integer.BYTES)
                    .putInt(value.length)
                    .array());
            digest.update(value);
        }
        return digest.digest();
    }

    public String hex(byte[] value) {
        return HexFormat.of().formatHex(value);
    }

    private void appendCanonical(JsonNode node, StringBuilder output) {
        if (node.isObject()) {
            output.append('{');
            List<String> names = new ArrayList<>(node.propertyNames());
            names.sort(String::compareTo);
            for (int index = 0; index < names.size(); index++) {
                if (index > 0) {
                    output.append(',');
                }
                String name = names.get(index);
                output.append(objectMapper.writeValueAsString(name)).append(':');
                appendCanonical(node.get(name), output);
            }
            output.append('}');
            return;
        }
        if (node.isArray()) {
            output.append('[');
            for (int index = 0; index < node.size(); index++) {
                if (index > 0) {
                    output.append(',');
                }
                appendCanonical(node.get(index), output);
            }
            output.append(']');
            return;
        }
        if (node.isString()) {
            output.append(objectMapper.writeValueAsString(node.stringValue()));
            return;
        }
        if (node.isNumber()) {
            var normalized = node.decimalValue().stripTrailingZeros();
            if (normalized.signum() == 0) {
                output.append('0');
            } else {
                output.append(normalized.toString()
                        .replace("E+", "e+")
                        .replace('E', 'e'));
            }
            return;
        }
        if (node.isBoolean()) {
            output.append(node.booleanValue());
            return;
        }
        if (node.isNull()) {
            output.append("null");
            return;
        }
        throw new IllegalArgumentException(
                "normalized payload contains unsupported JSON node");
    }

    private static MessageDigest newDigest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    public record CanonicalPayload(
            String json, byte[] sha256, String sha256Hex) {

        public CanonicalPayload {
            sha256 = sha256.clone();
        }

        @Override
        public byte[] sha256() {
            return sha256.clone();
        }
    }
}
