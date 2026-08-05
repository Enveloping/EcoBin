package org.enveloping.ecobin.recycling.api.persistence;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
public final class RecyclingOwnedPortFullnessAlertScopeRefFactory {

    public PortFullnessAlertScopeRef issue(
            long tenantKey, long organizationKey) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "port fullness alert scope requires a transaction");
        }
        var result = new PortFullnessAlertScopeRef(
                tenantKey, organizationKey,
                TransactionSynchronizationManager.getResourceMap());
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCompletion(int status) {
                        result.markCompleted();
                    }
                });
        return result;
    }
}
