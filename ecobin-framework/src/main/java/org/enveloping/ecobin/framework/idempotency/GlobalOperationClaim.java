package org.enveloping.ecobin.framework.idempotency;

/** Result of claiming a global operation UID. */
public record GlobalOperationClaim(
        boolean replay,
        GlobalOperationResult result) {

    public GlobalOperationClaim {
        if (replay != (result != null)) {
            throw new IllegalArgumentException(
                    "only a replay claim may carry a completed result");
        }
    }

    public static GlobalOperationClaim acquired() {
        return new GlobalOperationClaim(false, null);
    }

    public static GlobalOperationClaim replay(GlobalOperationResult result) {
        return new GlobalOperationClaim(true, result);
    }
}
