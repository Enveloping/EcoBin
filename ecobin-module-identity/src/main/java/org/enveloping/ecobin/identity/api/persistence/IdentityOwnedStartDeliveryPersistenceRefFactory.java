package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.application.persistence.StartDeliveryPersistenceRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Map;

/**
 * 与引用同包的 identity 私有发行实现，以保持引用构造器不可公开。
 */
@Component
final class IdentityOwnedStartDeliveryPersistenceRefFactory
        implements StartDeliveryPersistenceRefFactory {

    @Override
    public StartDeliveryOrganizationScopeRef issueOrganizationScope(
            long tenantKey,
            long organizationKey) {
        Map<Object, Object> resources = issuingResources();
        StartDeliveryOrganizationScopeRef reference =
                new StartDeliveryOrganizationScopeRef(
                tenantKey,
                organizationKey,
                resources);
        return register(reference, reference::markTransactionCompleted);
    }

    @Override
    public StartDeliveryWalletOwnerRef issueWalletOwner(
            long tenantKey,
            long organizationKey,
            long organizationUserKey) {
        Map<Object, Object> resources = issuingResources();
        StartDeliveryWalletOwnerRef reference =
                new StartDeliveryWalletOwnerRef(
                tenantKey,
                organizationKey,
                organizationUserKey,
                resources);
        return register(reference, reference::markTransactionCompleted);
    }

    @Override
    public DeliverySessionOrganizationUserRef issueDeliverySessionUser(
            long tenantKey,
            long organizationKey,
            long organizationUserKey) {
        Map<Object, Object> resources = issuingResources();
        DeliverySessionOrganizationUserRef reference =
                new DeliverySessionOrganizationUserRef(
                tenantKey,
                organizationKey,
                organizationUserKey,
                resources);
        return register(reference, reference::markTransactionCompleted);
    }

    @Override
    public StartDeliveryAuditActorRef issueAuditActor(
            long tenantKey,
            long organizationKey,
            long organizationUserKey) {
        Map<Object, Object> resources = issuingResources();
        StartDeliveryAuditActorRef reference =
                new StartDeliveryAuditActorRef(
                        tenantKey,
                        organizationKey,
                        organizationUserKey,
                        resources);
        return register(reference, reference::markTransactionCompleted);
    }

    private static Map<Object, Object> issuingResources() {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "persistence reference requires an active transaction");
        }
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            throw new IllegalStateException(
                    "persistence reference requires transaction synchronization");
        }
        Map<Object, Object> resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "persistence reference requires a bound transaction resource");
        }
        return resources;
    }

    private static <T> T register(
            T reference,
            Runnable completion) {
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCompletion(int status) {
                        completion.run();
                    }
                });
        return reference;
    }
}
