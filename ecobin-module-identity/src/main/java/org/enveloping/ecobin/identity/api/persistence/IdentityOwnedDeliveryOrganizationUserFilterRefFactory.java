package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.application.persistence.DeliveryOrganizationUserFilterRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Map;

@Component
final class IdentityOwnedDeliveryOrganizationUserFilterRefFactory
        implements DeliveryOrganizationUserFilterRefFactory {

    @Override
    public DeliveryOrganizationUserFilterRef issue(
            long tenantKey,
            long organizationKey,
            long organizationUserKey) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()) {
            throw new IllegalStateException(
                    "delivery user filter reference requires a read-only transaction");
        }
        if (!TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "delivery user filter reference requires transaction synchronization");
        }
        Map<Object, Object> resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "delivery user filter reference requires a bound transaction resource");
        }
        DeliveryOrganizationUserFilterRef reference =
                new DeliveryOrganizationUserFilterRef(
                        tenantKey,
                        organizationKey,
                        organizationUserKey,
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
