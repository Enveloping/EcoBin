package org.enveloping.ecobin.framework.web.v1;

import java.util.Map;

/**
 * Target HTTP failure carrying a stable machine code and safe details.
 */
public class TargetApiException extends RuntimeException {

    private final int status;
    private final String code;
    private final boolean retryable;
    private final Map<String, Object> details;

    public TargetApiException(int status, String code, String message) {
        this(status, code, message, false, Map.of());
    }

    public TargetApiException(
            int status,
            String code,
            String message,
            boolean retryable,
            Map<String, Object> details) {
        super(message);
        this.status = status;
        this.code = code;
        this.retryable = retryable;
        this.details = details == null ? Map.of() : Map.copyOf(details);
    }

    public int status() {
        return status;
    }

    public String code() {
        return code;
    }

    public boolean retryable() {
        return retryable;
    }

    public Map<String, Object> details() {
        return details;
    }
}
