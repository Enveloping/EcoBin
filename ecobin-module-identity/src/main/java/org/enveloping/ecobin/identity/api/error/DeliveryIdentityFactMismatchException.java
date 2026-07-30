package org.enveloping.ecobin.identity.api.error;

public final class DeliveryIdentityFactMismatchException
        extends RuntimeException {

    private final Reason reason;

    public DeliveryIdentityFactMismatchException(
            Reason reason,
            String message) {
        super(message);
        this.reason = reason;
    }

    public Reason reason() {
        return reason;
    }

    public enum Reason {
        ORGANIZATION_SCOPE_MISMATCH,
        ORGANIZATION_USER_MISMATCH,
        REVIEWER_MISMATCH
    }
}
