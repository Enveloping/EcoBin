package org.enveloping.ecobin.device.application.registration;

import org.enveloping.ecobin.device.api.persistence.RegistrationDeploymentRef;

public interface RegistrationDeploymentRefFactory {

    RegistrationDeploymentRef issue(
            long tenantKey,
            long organizationKey,
            long deploymentKey);
}
