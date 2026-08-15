package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.application.platformminiapp.FactoryOperatorPersistenceRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/** Keeps construction of factory-operator references inside identity. */
@Component
final class IdentityOwnedFactoryOperatorPersistenceRefFactory
        implements FactoryOperatorPersistenceRefFactory {

    @Override
    public FactoryOperatorPersistenceRef issue(long factoryOperatorKey) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "factory operator reference requires an active transaction");
        }
        var resources = TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "factory operator reference requires a bound resource");
        }
        FactoryOperatorPersistenceRef reference =
                new FactoryOperatorPersistenceRef(
                        factoryOperatorKey, resources);
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
