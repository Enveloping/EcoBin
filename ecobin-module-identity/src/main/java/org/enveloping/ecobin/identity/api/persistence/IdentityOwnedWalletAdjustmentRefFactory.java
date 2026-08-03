package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.application.persistence.WalletAdjustmentRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Map;
import java.util.UUID;

@Component
final class IdentityOwnedWalletAdjustmentRefFactory
        implements WalletAdjustmentRefFactory {

    @Override
    public WalletAdjustmentScopeRef issueScope(
            long tenantId, long organizationId) {
        WalletAdjustmentScopeRef ref = new WalletAdjustmentScopeRef(
                tenantId, organizationId, resources());
        return register(ref, ref::markTransactionCompleted);
    }

    @Override
    public WalletAdjustmentTargetRef issueTarget(
            long tenantId,
            long organizationId,
            long organizationUserId,
            UUID organizationUserUid,
            Long platformAdminId,
            Long staffAccountId) {
        WalletAdjustmentTargetRef ref = new WalletAdjustmentTargetRef(
                tenantId, organizationId, organizationUserId,
                organizationUserUid, platformAdminId, staffAccountId,
                resources());
        return register(ref, ref::markTransactionCompleted);
    }

    private static Map<Object, Object> resources() {
        if (!TransactionSynchronizationManager.isActualTransactionActive()
                || TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()) {
            throw new IllegalStateException(
                    "wallet adjustment refs require a writable transaction");
        }
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            throw new IllegalStateException(
                    "wallet adjustment refs require transaction synchronization");
        }
        Map<Object, Object> resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "wallet adjustment refs require a bound transaction resource");
        }
        return resources;
    }

    private static <T> T register(T ref, Runnable completion) {
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCompletion(int status) {
                        completion.run();
                    }
                });
        return ref;
    }
}
