package org.enveloping.ecobin.framework.reliability;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
final class TransactionBoundTrustedOrganizationInboxRefFactory
        implements TrustedOrganizationInboxRefFactory {

    @Override
    public TrustedOrganizationInboxRef issue(
            long inboxKey,
            long tenantKey,
            long organizationKey) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "trusted inbox reference requires a transaction");
        }
        var resources = TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "trusted inbox reference requires a bound resource");
        }
        TrustedOrganizationInboxRef reference =
                new TrustedOrganizationInboxRef(
                        inboxKey,
                        tenantKey,
                        organizationKey,
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
