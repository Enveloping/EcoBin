package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.application.persistence.StartCleanPersistenceRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Map;

/** 与关系引用同包的 identity 私有发行实现。 */
@Component
final class IdentityOwnedStartCleanPersistenceRefFactory
        implements StartCleanPersistenceRefFactory {

    @Override
    public CleanOrganizationScopeRef issueOrganizationScope(
            long tenantKey,
            long organizationKey) {
        Map<Object, Object> resources = issuingResources();
        var reference = new CleanOrganizationScopeRef(
                tenantKey, organizationKey, resources);
        return register(
                reference,
                reference::markTransactionCompleted);
    }

    @Override
    public CleanOrganizationUserRef issueOrganizationUser(
            long tenantKey,
            long organizationKey,
            long organizationUserKey) {
        Map<Object, Object> resources = issuingResources();
        var reference = new CleanOrganizationUserRef(
                tenantKey,
                organizationKey,
                organizationUserKey,
                resources);
        return register(
                reference,
                reference::markTransactionCompleted);
    }

    @Override
    public CleanAuditActorRef issueAuditActor(
            long tenantKey,
            long organizationKey,
            long organizationUserKey) {
        Map<Object, Object> resources = issuingResources();
        var reference = new CleanAuditActorRef(
                tenantKey,
                organizationKey,
                organizationUserKey,
                resources);
        return register(
                reference,
                reference::markTransactionCompleted);
    }

    private static Map<Object, Object> issuingResources() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "clean persistence reference requires an active transaction");
        }
        Map<Object, Object> resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "clean persistence reference requires a bound transaction resource");
        }
        return resources;
    }

    private static <T> T register(T reference, Runnable completion) {
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
