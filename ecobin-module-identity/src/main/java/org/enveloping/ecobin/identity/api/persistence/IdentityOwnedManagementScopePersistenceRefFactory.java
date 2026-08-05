package org.enveloping.ecobin.identity.api.persistence;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;

@Component
public class IdentityOwnedManagementScopePersistenceRefFactory {

    public ManagementScopePersistenceRef issue(
            Long tenantKey,
            List<Long> organizationKeys,
            List<String> organizationCodes,
            Long platformAdminKey,
            Long staffAccountKey) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "management scope reference requires a transaction");
        }
        Map<Object, Object> resources = new IdentityHashMap<>(
                TransactionSynchronizationManager.getResourceMap());
        var result = new ManagementScopePersistenceRef(
                tenantKey,
                organizationKeys,
                organizationCodes,
                platformAdminKey,
                staffAccountKey,
                resources);
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCompletion(int status) {
                        result.markTransactionCompleted();
                    }
                });
        return result;
    }
}
