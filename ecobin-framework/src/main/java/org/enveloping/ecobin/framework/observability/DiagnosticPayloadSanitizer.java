package org.enveloping.ecobin.framework.observability;

import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

import java.lang.reflect.Array;
import java.net.URI;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 为诊断日志提供统一的 JSON 脱敏、单行化、限长和异常净化。
 */
@Component
public final class DiagnosticPayloadSanitizer {

    public static final String REDACTED = "<redacted>";

    private static final Pattern AUTHORIZATION_HEADER = Pattern.compile(
            "(?i)authorization\\s*[\\\"']?\\s*[:=]\\s*"
                    + "(?:bearer|basic)\\s+[^\\s,;&}\\]]+");
    private static final Pattern COOKIE_HEADER = Pattern.compile(
            "(?i)(?:set-)?cookie\\s*[\\\"']?\\s*[:=]\\s*[^,]+");
    private static final Pattern SENSITIVE_ASSIGNMENT = Pattern.compile(
            "(?i)(password|passwd|secret(?:[_-]?(?:id|key))?|"
                    + "session[_-]?(?:key|token)|access[_-]?(?:token|key)|"
                    + "authorization|cookie|credential|private[_-]?key|"
                    + "signature|wechat[_-]?phone[_-]?code|"
                    + "phone[_-]?code|token)"
                    + "\\s*[\\\"']?\\s*([=:])\\s*([^\\s,;&}\\]]+)");
    private static final Pattern URL_WITH_QUERY = Pattern.compile(
            "(?i)https?://[^\\s\\\"']+\\?[^\\s\\\"']+");

    private final ObjectMapper objectMapper;

    public DiagnosticPayloadSanitizer(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    public String json(Object value, int maxLength) {
        if (value == null) {
            return "<none>";
        }
        try {
            Object parsed = value instanceof String text
                    ? objectMapper.readValue(text, Object.class)
                    : objectMapper.readValue(
                            objectMapper.writeValueAsString(value),
                            Object.class);
            return limit(
                    objectMapper.writeValueAsString(redact(parsed)),
                    maxLength);
        } catch (Exception malformed) {
            if (value instanceof String text) {
                return "<malformed JSON, " + text.length()
                        + " characters>";
            }
            return "<unserializable payload type="
                    + value.getClass().getSimpleName() + ">";
        }
    }

    public String text(String value, int maxLength) {
        if (value == null || value.isBlank()) {
            return "<none>";
        }
        StringBuilder singleLine = new StringBuilder(value.length());
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            singleLine.append(Character.isISOControl(character)
                    ? ' ' : character);
        }
        String sanitized = redactSensitiveAssignments(
                redactUrlQueries(singleLine.toString()));
        return limit(sanitized, maxLength);
    }

    public Throwable throwable(Throwable failure, int maxMessageLength) {
        return sanitizeThrowable(failure, maxMessageLength, 0);
    }

    public static boolean isSensitiveField(String name) {
        String normalized = name == null
                ? ""
                : name.replaceAll("[^A-Za-z0-9]", "")
                .toLowerCase(Locale.ROOT);
        return normalized.contains("password")
                || normalized.contains("passwd")
                || normalized.contains("secret")
                || normalized.contains("credential")
                || normalized.contains("privatekey")
                || normalized.contains("sessionkey")
                || normalized.contains("accesskey")
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

    private Object redact(Object value) {
        if (value instanceof Map<?, ?> map) {
            Map<String, Object> sanitized = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                String name = String.valueOf(entry.getKey());
                sanitized.put(
                        name,
                        isSensitiveField(name)
                                ? REDACTED
                                : redact(entry.getValue()));
            }
            return sanitized;
        }
        if (value instanceof Collection<?> collection) {
            List<Object> sanitized = new ArrayList<>(collection.size());
            for (Object item : collection) {
                sanitized.add(redact(item));
            }
            return sanitized;
        }
        if (value != null && value.getClass().isArray()) {
            int length = Array.getLength(value);
            List<Object> sanitized = new ArrayList<>(length);
            for (int index = 0; index < length; index++) {
                sanitized.add(redact(Array.get(value, index)));
            }
            return sanitized;
        }
        if (value instanceof String text) {
            return text(text, 4_096);
        }
        return value;
    }

    private Throwable sanitizeThrowable(
            Throwable failure,
            int maxMessageLength,
            int depth) {
        if (failure == null) {
            return null;
        }
        String message = failure.getClass().getName();
        if (failure.getMessage() != null
                && !failure.getMessage().isBlank()) {
            message += ": " + text(
                    failure.getMessage(), maxMessageLength);
        }
        SanitizedDiagnosticException sanitized =
                new SanitizedDiagnosticException(message);
        sanitized.setStackTrace(failure.getStackTrace());
        if (depth < 8 && failure.getCause() != null
                && failure.getCause() != failure) {
            sanitized.initCause(sanitizeThrowable(
                    failure.getCause(), maxMessageLength, depth + 1));
        }
        if (depth < 8) {
            for (Throwable suppressed : failure.getSuppressed()) {
                sanitized.addSuppressed(sanitizeThrowable(
                        suppressed, maxMessageLength, depth + 1));
            }
        }
        return sanitized;
    }

    private static String redactSensitiveAssignments(String value) {
        String headersRedacted = AUTHORIZATION_HEADER.matcher(value)
                .replaceAll("authorization=" + REDACTED);
        headersRedacted = COOKIE_HEADER.matcher(headersRedacted)
                .replaceAll("cookie=" + REDACTED);
        Matcher matcher = SENSITIVE_ASSIGNMENT.matcher(headersRedacted);
        StringBuilder sanitized = new StringBuilder(value.length());
        while (matcher.find()) {
            matcher.appendReplacement(
                    sanitized,
                    Matcher.quoteReplacement(
                            matcher.group(1) + matcher.group(2)
                                    + REDACTED));
        }
        matcher.appendTail(sanitized);
        return sanitized.toString();
    }

    private static String redactUrlQueries(String value) {
        Matcher matcher = URL_WITH_QUERY.matcher(value);
        StringBuilder sanitized = new StringBuilder(value.length());
        while (matcher.find()) {
            String replacement;
            try {
                URI uri = URI.create(matcher.group());
                replacement = new URI(
                        uri.getScheme(),
                        uri.getAuthority(),
                        uri.getPath(),
                        "<redacted>",
                        uri.getFragment()).toString();
            } catch (Exception ignored) {
                replacement = "<redacted-url-query>";
            }
            matcher.appendReplacement(
                    sanitized, Matcher.quoteReplacement(replacement));
        }
        matcher.appendTail(sanitized);
        return sanitized.toString();
    }

    private static String limit(String value, int maxLength) {
        int safeLimit = Math.max(64, maxLength);
        if (value.length() <= safeLimit) {
            return value;
        }
        return value.substring(0, safeLimit)
                + "…<truncated totalCharacters=" + value.length() + ">";
    }

    private static final class SanitizedDiagnosticException
            extends RuntimeException {

        private SanitizedDiagnosticException(String message) {
            super(message);
        }
    }
}
