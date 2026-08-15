package org.enveloping.ecobin.framework.idempotency;

import java.util.Objects;
import java.util.UUID;
import java.util.regex.Pattern;

/** Complete immutable identity of one globally unique operation UID. */
public record GlobalOperationBinding(
        UUID operationUid,
        String actorKind,
        UUID actorUid,
        String scopeDigest,
        String actionCode,
        String targetType,
        String targetStableKey,
        String requestDigest) {

    private static final Pattern SHA_256 = Pattern.compile("^[0-9a-f]{64}$");

    public GlobalOperationBinding {
        Objects.requireNonNull(operationUid, "operationUid");
        Objects.requireNonNull(actorUid, "actorUid");
        actorKind = required(actorKind, "actorKind");
        actionCode = required(actionCode, "actionCode");
        targetType = required(targetType, "targetType");
        targetStableKey = required(targetStableKey, "targetStableKey");
        scopeDigest = digest(scopeDigest, "scopeDigest");
        requestDigest = digest(requestDigest, "requestDigest");
    }

    private static String required(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
        return value;
    }

    private static String digest(String value, String field) {
        if (value == null || !SHA_256.matcher(value).matches()) {
            throw new IllegalArgumentException(
                    field + " must be exactly 64 lowercase hexadecimal characters");
        }
        return value;
    }
}
