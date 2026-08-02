package org.enveloping.ecobin.device.api.result;

import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRef;

import java.util.Objects;

public record TrustedDeviceTransportEvent(
        TrustedPlatformInboxRef sourceInbox,
        int normalizedSchemaVersion,
        String normalizedPayload) {

    public TrustedDeviceTransportEvent {
        Objects.requireNonNull(sourceInbox, "sourceInbox");
        Objects.requireNonNull(normalizedPayload, "normalizedPayload");
        if (normalizedSchemaVersion != 1) {
            throw new IllegalArgumentException(
                    "unsupported transport event schema version");
        }
    }
}
