package org.enveloping.ecobin.framework.reliability;

import java.util.UUID;

/**
 * Durable isolation seam for a trusted inbox item whose domain identity
 * conflicts with already accepted authoritative data.
 */
public interface TrustedInboxQuarantinePort {

    UUID quarantine(
            TrustedOrganizationInboxRef sourceInbox,
            String reasonCode,
            String redactedDiagnostic);

    default UUID quarantineIdentityConflict(
            TrustedOrganizationInboxRef sourceInbox,
            String redactedDiagnostic) {
        return quarantine(
                sourceInbox,
                "IDENTITY_CONTENT_CONFLICT",
                redactedDiagnostic);
    }
}
