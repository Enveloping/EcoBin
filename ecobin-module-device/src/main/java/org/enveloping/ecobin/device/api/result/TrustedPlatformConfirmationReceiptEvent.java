package org.enveloping.ecobin.device.api.result;

import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRef;

import java.util.Objects;

/** A trusted device confirmation receipt whose authoritative scope is platform. */
public record TrustedPlatformConfirmationReceiptEvent(
        TrustedPlatformInboxRef sourceInbox,
        int normalizedSchemaVersion,
        String normalizedPayload) {

    public TrustedPlatformConfirmationReceiptEvent {
        Objects.requireNonNull(sourceInbox, "sourceInbox");
        Objects.requireNonNull(normalizedPayload, "normalizedPayload");
        if (normalizedSchemaVersion <= 0) {
            throw new IllegalArgumentException(
                    "normalizedSchemaVersion must be positive");
        }
    }
}
