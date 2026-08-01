package org.enveloping.ecobin.identity.application.persistence;

import org.enveloping.ecobin.identity.api.persistence.DeliveryOrganizationUserFilterRef;

public interface DeliveryOrganizationUserFilterRefFactory {

    DeliveryOrganizationUserFilterRef issue(
            long tenantKey,
            long organizationKey,
            long organizationUserKey);
}
