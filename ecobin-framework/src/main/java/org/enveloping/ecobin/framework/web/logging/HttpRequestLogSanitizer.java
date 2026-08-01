package org.enveloping.ecobin.framework.web.logging;

import tools.jackson.databind.ObjectMapper;

import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * 将查询参数和请求体转换为单行、限长且不包含凭证的诊断文本。
 */
final class HttpRequestLogSanitizer {

    static final String REDACTED = "<redacted>";
    private static final int MAX_LOG_VALUE_LENGTH = 512;

    private final ObjectMapper objectMapper;

    HttpRequestLogSanitizer(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    String query(String rawQuery) {
        if (rawQuery == null || rawQuery.isBlank()) {
            return "<none>";
        }
        Map<String, List<String>> parameters = new LinkedHashMap<>();
        for (String pair : rawQuery.split("&", -1)) {
            int separator = pair.indexOf('=');
            String rawName = separator < 0
                    ? pair : pair.substring(0, separator);
            String rawValue = separator < 0
                    ? "" : pair.substring(separator + 1);
            String name = decode(rawName);
            String value = sensitive(name)
                    ? REDACTED
                    : safeValue(decode(rawValue));
            parameters.computeIfAbsent(
                    safeValue(name), ignored -> new ArrayList<>())
                    .add(value);
        }
        return parameters.toString();
    }

    String body(
            byte[] content,
            String contentType,
            boolean truncated) {
        if (content.length == 0) {
            return "<empty>";
        }
        if (truncated) {
            return "<body truncated before logging>";
        }
        String body = new String(content, StandardCharsets.UTF_8);
        if (json(contentType)) {
            return jsonBody(body);
        }
        if (form(contentType)) {
            return query(body);
        }
        return "<body not logged for content type "
                + safeValue(contentType) + ">";
    }

    static String safeValue(String value) {
        if (value == null || value.isBlank()) {
            return "<none>";
        }
        StringBuilder safe = new StringBuilder(
                Math.min(value.length(), MAX_LOG_VALUE_LENGTH));
        for (int index = 0;
             index < value.length()
                     && safe.length() < MAX_LOG_VALUE_LENGTH;
             index++) {
            char character = value.charAt(index);
            safe.append(Character.isISOControl(character)
                    ? '?' : character);
        }
        if (value.length() > MAX_LOG_VALUE_LENGTH) {
            safe.append("…");
        }
        return safe.toString();
    }

    private String jsonBody(String body) {
        try {
            Object parsed = objectMapper.readValue(body, Object.class);
            return objectMapper.writeValueAsString(redact(parsed));
        } catch (Exception ignored) {
            return "<malformed JSON body, " + body.length()
                    + " characters>";
        }
    }

    private Object redact(Object value) {
        if (value instanceof Map<?, ?> map) {
            Map<String, Object> sanitized = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                String name = String.valueOf(entry.getKey());
                sanitized.put(
                        name,
                        sensitive(name)
                                ? REDACTED
                                : redact(entry.getValue()));
            }
            return sanitized;
        }
        if (value instanceof Collection<?> collection) {
            List<Object> sanitized =
                    new ArrayList<>(collection.size());
            for (Object item : collection) {
                sanitized.add(redact(item));
            }
            return sanitized;
        }
        if (value instanceof String text) {
            return safeValue(text);
        }
        return value;
    }

    private static boolean sensitive(String name) {
        String normalized = name == null
                ? ""
                : name.replaceAll("[^A-Za-z0-9]", "")
                .toLowerCase(Locale.ROOT);
        return normalized.contains("password")
                || normalized.contains("secret")
                || normalized.contains("credential")
                || normalized.contains("privatekey")
                || normalized.contains("sessionkey")
                || normalized.contains("authorization")
                || normalized.contains("cookie")
                || normalized.contains("signature")
                || normalized.contains("ciphertext")
                || normalized.contains("idcard")
                || normalized.contains("bankcard")
                || normalized.contains("accountnumber")
                || normalized.contains("phone")
                || normalized.endsWith("token")
                || normalized.equals("token")
                || normalized.equals("wxlogincode")
                || normalized.equals("jscode")
                || normalized.equals("logincode");
    }

    private static String decode(String encoded) {
        try {
            return URLDecoder.decode(
                    encoded, StandardCharsets.UTF_8);
        } catch (IllegalArgumentException malformedEncoding) {
            return "<malformed-url-encoding>";
        }
    }

    private static boolean json(String contentType) {
        String normalized = normalizeContentType(contentType);
        return normalized.equals("application/json")
                || normalized.endsWith("+json");
    }

    private static boolean form(String contentType) {
        return normalizeContentType(contentType)
                .equals("application/x-www-form-urlencoded");
    }

    private static String normalizeContentType(String contentType) {
        if (contentType == null) {
            return "";
        }
        int parameters = contentType.indexOf(';');
        String mediaType = parameters < 0
                ? contentType : contentType.substring(0, parameters);
        return mediaType.trim().toLowerCase(Locale.ROOT);
    }
}
