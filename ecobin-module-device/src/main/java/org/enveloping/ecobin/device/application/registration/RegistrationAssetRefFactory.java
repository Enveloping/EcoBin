package org.enveloping.ecobin.device.application.registration;

import org.enveloping.ecobin.device.api.persistence.RegistrationAssetRef;

public interface RegistrationAssetRefFactory {

    RegistrationAssetRef issue(
            long tenantKey,
            long organizationKey,
            long assetKey);
}
