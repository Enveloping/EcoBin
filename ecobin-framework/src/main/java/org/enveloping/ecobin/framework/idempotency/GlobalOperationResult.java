package org.enveloping.ecobin.framework.idempotency;

import java.util.Objects;
import java.util.UUID;

/** Stable result needed to replay a completed operation without repeating it. */
public record GlobalOperationResult(
        UUID resourceUid,
        String state,
        long version) {

    public GlobalOperationResult {
        Objects.requireNonNull(resourceUid, "resourceUid");
        if (state == null || state.isBlank()) {
            throw new IllegalArgumentException("state must not be blank");
        }
    }
}
