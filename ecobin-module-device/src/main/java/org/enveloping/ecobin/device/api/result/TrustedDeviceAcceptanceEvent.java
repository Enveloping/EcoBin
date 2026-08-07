package org.enveloping.ecobin.device.api.result;

import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRef;

import java.util.Objects;

public record TrustedDeviceAcceptanceEvent(
        TrustedPlatformInboxRef sourceInbox,
        int normalizedSchemaVersion,
        String normalizedPayload) {

    public TrustedDeviceAcceptanceEvent {
        Objects.requireNonNull(sourceInbox, "sourceInbox");
        Objects.requireNonNull(normalizedPayload, "normalizedPayload");
        if (normalizedSchemaVersion != 2) {
            throw new IllegalArgumentException(
                    "unsupported acceptance evidence schema version");
        }
    }
}
