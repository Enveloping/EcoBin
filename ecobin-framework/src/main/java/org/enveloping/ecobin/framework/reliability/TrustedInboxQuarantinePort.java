package org.enveloping.ecobin.framework.reliability;

import java.util.UUID;

/**
 * Durable isolation seam for a trusted inbox item whose domain identity
 * conflicts with already accepted authoritative data.
 */
public interface TrustedInboxQuarantinePort {

    UUID quarantineIdentityConflict(
            TrustedOrganizationInboxRef sourceInbox,
            String redactedDiagnostic);
}
