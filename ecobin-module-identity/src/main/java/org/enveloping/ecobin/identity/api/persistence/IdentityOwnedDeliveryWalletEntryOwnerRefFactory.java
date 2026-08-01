package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.application.persistence.DeliveryWalletEntryOwnerRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Map;
import java.util.UUID;

/**
 * 与可信组合引用同包的 identity 私有发行实现。
 */
@Component
final class IdentityOwnedDeliveryWalletEntryOwnerRefFactory
        implements DeliveryWalletEntryOwnerRefFactory {

    @Override
    public DeliveryWalletEntryOwnerRef issue(
            long tenantKey,
            long organizationKey,
            long organizationUserKey,
            UUID organizationUserUid) {
        Map<Object, Object> resources = issuingResources();
        DeliveryWalletEntryOwnerRef reference =
                new DeliveryWalletEntryOwnerRef(
                        tenantKey,
                        organizationKey,
                        organizationUserKey,
                        organizationUserUid,
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

    private static Map<Object, Object> issuingResources() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "wallet entry owner reference requires "
                            + "an active transaction");
        }
        if (!TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "wallet entry owner reference requires "
                            + "transaction synchronization");
        }
        Map<Object, Object> resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "wallet entry owner reference requires "
                            + "a bound transaction resource");
        }
        return resources;
    }
}
