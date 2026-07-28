package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.identity.api.persistence.DeviceScopePersistenceRef;

public interface DeviceScopePersistenceRefFactory {

    DeviceScopePersistenceRef issue(
            Long tenantKey,
            Long organizationKey,
            Long platformAdminKey,
            Long staffAccountKey);
}
