package org.enveloping.ecobin.identity.web.v1;

public record TargetApiEnvelope<T>(
        String code,
        T data,
        String requestId) {

    public static <T> TargetApiEnvelope<T> ok(T data, String requestId) {
        return new TargetApiEnvelope<>("OK", data, requestId);
    }
}
