package org.enveloping.ecobin.identity.api.error;

public final class WechatExchangeException extends RuntimeException {

    private final Reason reason;

    public WechatExchangeException(Reason reason, String message) {
        super(message);
        this.reason = reason;
    }

    public WechatExchangeException(
            Reason reason,
            String message,
            Throwable cause) {
        super(message, cause);
        this.reason = reason;
    }

    public Reason reason() {
        return reason;
    }

    public enum Reason {
        INVALID_CODE,
        SERVICE_UNAVAILABLE
    }
}
