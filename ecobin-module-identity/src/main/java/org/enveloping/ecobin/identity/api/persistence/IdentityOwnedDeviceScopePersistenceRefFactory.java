package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.application.security.DeviceScopePersistenceRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
final class IdentityOwnedDeviceScopePersistenceRefFactory
        implements DeviceScopePersistenceRefFactory {

    @Override
    public DeviceScopePersistenceRef issue(
            Long tenantKey,
            Long organizationKey,
            Long platformAdminKey,
            Long staffAccountKey) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "device scope reference requires an active transaction");
        }
        var resources = TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "device scope reference requires a bound resource");
        }
        DeviceScopePersistenceRef reference =
                new DeviceScopePersistenceRef(
                        tenantKey,
                        organizationKey,
                        platformAdminKey,
                        staffAccountKey,
                        resources);
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
