package org.enveloping.ecobin.device.api.result;

import java.util.Objects;
import java.util.UUID;

/**
 * Public Device identities corresponding to one caller token.
 *
 * <p>An unresolved or scope-mismatched tuple is represented by all four
 * public fact fields being {@code null}; partial results are rejected.</p>
 */
public record DeliveryOrderDeviceFacts(
        String token,
        UUID eventUid,
        UUID sessionUid,
        String deploymentCode,
        Integer portNo) {

    public DeliveryOrderDeviceFacts {
        if (token == null || token.isBlank()) {
            throw new IllegalArgumentException(
                    "token must not be blank");
        }
        boolean allPresent = eventUid != null
                && sessionUid != null
                && deploymentCode != null
                && portNo != null;
        boolean allMissing = eventUid == null
                && sessionUid == null
                && deploymentCode == null
                && portNo == null;
        if (!allPresent && !allMissing) {
            throw new IllegalArgumentException(
                    "device facts must be fully resolved or fully missing");
        }
        if (allPresent) {
            Objects.requireNonNull(
                    deploymentCode,
                    "deploymentCode");
            if (deploymentCode.isBlank()) {
                throw new IllegalArgumentException(
                        "deploymentCode must not be blank");
            }
            if (portNo < 1 || portNo > 6) {
                throw new IllegalArgumentException(
                        "portNo must be between 1 and 6");
            }
        }
    }

    public boolean resolved() {
        return eventUid != null;
    }

    public static DeliveryOrderDeviceFacts missing(String token) {
        return new DeliveryOrderDeviceFacts(
                token,
                null,
                null,
                null,
                null);
    }
}
