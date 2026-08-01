package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.identity.api.persistence.DeliveryScopePersistenceRef;

public interface DeliveryScopePersistenceRefFactory {

    DeliveryScopePersistenceRef issue(
            long tenantKey,
            long organizationKey,
            Long platformAdminKey,
            Long staffAccountKey);
}
