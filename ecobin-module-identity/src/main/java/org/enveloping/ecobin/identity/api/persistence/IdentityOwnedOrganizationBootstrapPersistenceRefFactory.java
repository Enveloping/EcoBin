package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.application.persistence.OrganizationBootstrapPersistenceRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/**
 * 与引用同包的 identity 私有发行实现，以保持引用构造器不可公开。
 */
@Component
final class IdentityOwnedOrganizationBootstrapPersistenceRefFactory
        implements OrganizationBootstrapPersistenceRefFactory {

    @Override
    public OrganizationBootstrapPersistenceRef issue(
            long tenantKey,
            long organizationKey) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "organization bootstrap reference requires an active transaction");
        }
        var resources = TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "organization bootstrap reference requires a bound resource");
        }
        OrganizationBootstrapPersistenceRef reference =
                new OrganizationBootstrapPersistenceRef(
                        tenantKey, organizationKey, resources);
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
