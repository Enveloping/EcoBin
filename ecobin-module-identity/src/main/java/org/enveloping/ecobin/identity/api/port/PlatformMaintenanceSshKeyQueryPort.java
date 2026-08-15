package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.result.ActivePlatformMaintenanceSshKey;

import java.util.Optional;
import java.util.UUID;

/**
 * Identity-owned lookup used by remote-support workflows. The caller only
 * receives a key while both the administrator and the key remain active.
 * This operation must join the writable transaction that will persist the
 * remote-support session.
 */
public interface PlatformMaintenanceSshKeyQueryPort {

    Optional<ActivePlatformMaintenanceSshKey> resolveActive(
            UUID platformAdminUid,
            UUID maintenanceSshKeyUid);
}
