package org.enveloping.ecobin.device.api.persistence;

import org.enveloping.ecobin.device.application.registration.RegistrationAssetRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
final class DeviceOwnedRegistrationAssetRefFactory
        implements RegistrationAssetRefFactory {

    @Override
    public RegistrationAssetRef issue(
            long tenantKey,
            long organizationKey,
            long assetKey) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "registration asset reference requires an active transaction");
        }
        var resources = TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "registration asset reference requires a bound transaction resource");
        }
        RegistrationAssetRef reference = new RegistrationAssetRef(
                tenantKey, organizationKey, assetKey, resources);
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
