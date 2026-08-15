package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.persistence.MaintenanceSshKeyPersistenceRef;

import java.util.Objects;
import java.util.UUID;

/**
 * Public identity fact for issuing a short-lived maintenance SSH certificate.
 * No operator private-key material or identity database key crosses this port;
 * persistence participation is only possible through the opaque reference.
 */
public record ActivePlatformMaintenanceSshKey(
        UUID platformAdminUid,
        UUID maintenanceSshKeyUid,
        String label,
        String publicKey,
        String fingerprintSha256,
        MaintenanceSshKeyPersistenceRef persistenceRef) {

    public ActivePlatformMaintenanceSshKey {
        Objects.requireNonNull(platformAdminUid, "platformAdminUid");
        Objects.requireNonNull(maintenanceSshKeyUid, "maintenanceSshKeyUid");
        Objects.requireNonNull(label, "label");
        Objects.requireNonNull(publicKey, "publicKey");
        Objects.requireNonNull(fingerprintSha256, "fingerprintSha256");
        Objects.requireNonNull(persistenceRef, "persistenceRef");
    }
}
