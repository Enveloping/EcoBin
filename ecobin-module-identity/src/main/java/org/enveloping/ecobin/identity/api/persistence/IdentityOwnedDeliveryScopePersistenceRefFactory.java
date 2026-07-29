package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.application.security.DeliveryScopePersistenceRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Map;

@Component
final class IdentityOwnedDeliveryScopePersistenceRefFactory
        implements DeliveryScopePersistenceRefFactory {

    @Override
    public DeliveryScopePersistenceRef issue(
            long tenantKey,
            long organizationKey,
            Long platformAdminKey,
            Long staffAccountKey) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "delivery scope reference requires an active transaction");
        }
        if (!TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "delivery scope reference requires transaction synchronization");
        }
        Map<Object, Object> resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "delivery scope reference requires a bound transaction resource");
        }
        DeliveryScopePersistenceRef reference =
                new DeliveryScopePersistenceRef(
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
