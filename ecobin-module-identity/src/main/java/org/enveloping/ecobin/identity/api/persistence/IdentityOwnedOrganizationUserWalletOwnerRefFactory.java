package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.application.persistence.OrganizationUserWalletOwnerRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/**
 * 与引用同包的 identity 私有发行实现，以保持引用构造器不可公开。
 */
@Component
final class IdentityOwnedOrganizationUserWalletOwnerRefFactory
        implements OrganizationUserWalletOwnerRefFactory {

    @Override
    public OrganizationUserWalletOwnerRef issue(
            long tenantKey,
            long organizationKey,
            long organizationUserKey) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException("persistence reference requires an active transaction");
        }
        var resources = TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException("persistence reference requires a bound transaction resource");
        }
        OrganizationUserWalletOwnerRef reference = new OrganizationUserWalletOwnerRef(
                tenantKey, organizationKey, organizationUserKey, resources);
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCompletion(int status) {
                reference.markTransactionCompleted();
            }
        });
        return reference;
    }
}
