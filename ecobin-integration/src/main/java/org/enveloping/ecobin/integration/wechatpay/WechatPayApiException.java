package org.enveloping.ecobin.integration.wechatpay;

final class WechatPayApiException extends RuntimeException {

    private final int status;
    private final String code;

    WechatPayApiException(int status, String code, String message) {
        super(message == null || message.isBlank() ? code : message);
        this.status = status;
        this.code = code;
    }

    int status() {
        return status;
    }

    String code() {
        return code;
    }

    boolean retryable() {
        return status >= 500 || status == 429
                || "SYSTEM_ERROR".equals(code)
                || "FREQUENCY_LIMIT_EXCEED".equals(code)
                || "RATELIMIT_EXCEEDED".equals(code)
                || "FREQUENCY_LIMIT".equals(code)
                || "ALREADY_EXISTS".equals(code);
    }
}
