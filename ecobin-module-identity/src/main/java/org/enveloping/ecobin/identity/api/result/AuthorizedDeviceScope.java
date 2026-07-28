package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.persistence.DeviceScopePersistenceRef;

import java.util.UUID;

/**
 * Safe device authorization result. Internal relationship keys remain inside
 * the single-use persistence reference.
 */
public record AuthorizedDeviceScope(
        boolean platformActor,
        UUID principalUid,
        UUID sessionUid,
        String actorDisplayName,
        String tenantCode,
        String organizationCode,
        boolean tenantEnabled,
        boolean organizationEnabled,
        DeviceScopePersistenceRef persistenceRef) {
}
