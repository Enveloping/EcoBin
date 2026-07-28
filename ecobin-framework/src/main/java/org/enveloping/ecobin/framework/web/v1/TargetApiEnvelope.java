package org.enveloping.ecobin.framework.web.v1;

/**
 * Stable response envelope shared by every target V1 HTTP interface.
 */
public record TargetApiEnvelope<T>(
        String code,
        T data,
        String requestId) {

    public static <T> TargetApiEnvelope<T> ok(T data, String requestId) {
        return new TargetApiEnvelope<>("OK", data, requestId);
    }
}
