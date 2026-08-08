package org.enveloping.ecobin.device.api.result;

import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRef;

import java.util.Objects;

public record TrustedDeviceInboxEvent(
        TrustedOrganizationInboxRef sourceInbox,
        String messageKind,
        int normalizedSchemaVersion,
        String normalizedPayload) {

    public static final int CURRENT_NORMALIZED_SCHEMA_VERSION = 2;

    public TrustedDeviceInboxEvent {
        Objects.requireNonNull(sourceInbox, "sourceInbox");
        Objects.requireNonNull(messageKind, "messageKind");
        Objects.requireNonNull(normalizedPayload, "normalizedPayload");
        if (normalizedSchemaVersion <= 0) {
            throw new IllegalArgumentException(
                    "normalizedSchemaVersion must be positive");
        }
    }
}
