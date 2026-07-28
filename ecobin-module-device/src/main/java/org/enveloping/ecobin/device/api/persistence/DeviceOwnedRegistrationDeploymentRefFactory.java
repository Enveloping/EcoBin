package org.enveloping.ecobin.device.api.persistence;

import org.enveloping.ecobin.device.application.registration.RegistrationDeploymentRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
final class DeviceOwnedRegistrationDeploymentRefFactory
        implements RegistrationDeploymentRefFactory {

    @Override
    public RegistrationDeploymentRef issue(
            long tenantKey,
            long organizationKey,
            long deploymentKey) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "registration deployment reference requires an active transaction");
        }
        var resources = TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "registration deployment reference requires a bound transaction resource");
        }
        RegistrationDeploymentRef reference = new RegistrationDeploymentRef(
                tenantKey, organizationKey, deploymentKey, resources);
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCompletion(int status) {
                        reference.markTransactionCompleted();
                    }
                });
        return reference;
    }
}
