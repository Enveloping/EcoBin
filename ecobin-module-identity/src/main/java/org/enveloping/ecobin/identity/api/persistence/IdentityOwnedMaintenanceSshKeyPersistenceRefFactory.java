package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.application.maintenance.MaintenanceSshKeyPersistenceRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/** Keeps construction of maintenance-key references inside identity. */
@Component
final class IdentityOwnedMaintenanceSshKeyPersistenceRefFactory
        implements MaintenanceSshKeyPersistenceRefFactory {

    @Override
    public MaintenanceSshKeyPersistenceRef issue(
            long maintenanceSshKeyKey) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "maintenance SSH key reference requires an active transaction");
        }
        var resources = TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "maintenance SSH key reference requires a bound resource");
        }
        MaintenanceSshKeyPersistenceRef reference =
                new MaintenanceSshKeyPersistenceRef(
                        maintenanceSshKeyKey, resources);
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
