package org.enveloping.ecobin.identity.application.maintenance;

import org.enveloping.ecobin.identity.api.persistence.MaintenanceSshKeyPersistenceRef;

/** Identity-internal bridge used to issue opaque maintenance-key references. */
public interface MaintenanceSshKeyPersistenceRefFactory {

    MaintenanceSshKeyPersistenceRef issue(long maintenanceSshKeyKey);
}
