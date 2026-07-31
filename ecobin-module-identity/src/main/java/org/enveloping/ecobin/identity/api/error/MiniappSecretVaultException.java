package org.enveloping.ecobin.identity.api.error;

/**
 * Safe, secret-free failure raised by the external mini-program secret vault.
 */
public final class MiniappSecretVaultException extends RuntimeException {

    public enum Reason {
        IDEMPOTENCY_CONFLICT,
        UNAVAILABLE
    }

    private final Reason reason;

    public MiniappSecretVaultException(Reason reason, String message) {
        super(message);
        this.reason = reason;
    }

    public MiniappSecretVaultException(
            Reason reason,
            String message,
            Throwable cause) {
        super(message, cause);
        this.reason = reason;
    }

    public Reason reason() {
        return reason;
    }
}
