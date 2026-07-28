package org.enveloping.ecobin.framework.web.v1;

import java.util.Map;

public record TargetProblemDetail(
        String code,
        String message,
        String requestId,
        boolean retryable,
        Map<String, Object> details) {

    public TargetProblemDetail {
        details = details == null ? Map.of() : Map.copyOf(details);
    }
}
