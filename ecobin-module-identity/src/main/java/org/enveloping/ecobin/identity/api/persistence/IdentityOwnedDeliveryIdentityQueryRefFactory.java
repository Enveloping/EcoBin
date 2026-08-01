package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.application.persistence.DeliveryIdentityQueryRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Map;
import java.util.UUID;

/**
 * 与投递、钱包查询引用同包的 identity 私有发行实现。
 */
@Component
final class IdentityOwnedDeliveryIdentityQueryRefFactory
        implements DeliveryIdentityQueryRefFactory {

    @Override
    public DeliveryQueryOrganizationUserRef issueDeliveryQueryUser(
            long tenantKey,
            long organizationKey,
            long organizationUserKey,
            UUID organizationUserUid) {
        Map<Object, Object> resources = issuingResources();
        DeliveryQueryOrganizationUserRef reference =
                new DeliveryQueryOrganizationUserRef(
                        tenantKey,
                        organizationKey,
                        organizationUserKey,
                        organizationUserUid,
                        resources);
        return register(reference, reference::markTransactionCompleted);
    }

    @Override
    public DeliveryWalletQueryOwnerRef issueWalletQueryOwner(
            long tenantKey,
            long organizationKey,
            long organizationUserKey,
            UUID organizationUserUid) {
        Map<Object, Object> resources = issuingResources();
        DeliveryWalletQueryOwnerRef reference =
                new DeliveryWalletQueryOwnerRef(
                        tenantKey,
                        organizationKey,
                        organizationUserKey,
                        organizationUserUid,
                        resources);
        return register(reference, reference::markTransactionCompleted);
    }

    @Override
    public WalletQueryScopeRef issueWalletScope(
            long tenantKey,
            long organizationKey,
            Long organizationUserKey,
            UUID organizationUserUid) {
        Map<Object, Object> resources = issuingResources();
        WalletQueryScopeRef reference = new WalletQueryScopeRef(
                tenantKey,
                organizationKey,
                organizationUserKey,
                organizationUserUid,
                resources);
        return register(reference, reference::markTransactionCompleted);
    }

    private static Map<Object, Object> issuingResources() {
        if (!TransactionSynchronizationManager.isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()) {
            throw new IllegalStateException(
                    "identity read query reference requires "
                            + "an active read-only transaction");
        }
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            throw new IllegalStateException(
                    "identity read query reference requires "
                            + "transaction synchronization");
        }
        Map<Object, Object> resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "identity read query reference requires "
                            + "a bound transaction resource");
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
