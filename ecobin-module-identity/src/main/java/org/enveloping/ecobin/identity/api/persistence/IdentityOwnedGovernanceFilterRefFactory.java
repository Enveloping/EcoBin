package org.enveloping.ecobin.identity.api.persistence;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;

@Component
public final class IdentityOwnedGovernanceFilterRefFactory {

    public GovernanceIdentityFilterRef issue(
            boolean organizationRequested,
            List<Long> organizationKeys,
            boolean actorRequested,
            List<Long> platformAdminKeys,
            List<Long> staffAccountKeys,
            List<Long> organizationUserKeys) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "governance identity filter requires a transaction");
        }
        var result = new GovernanceIdentityFilterRef(
                organizationRequested, organizationKeys,
                actorRequested, platformAdminKeys, staffAccountKeys,
                organizationUserKeys,
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
