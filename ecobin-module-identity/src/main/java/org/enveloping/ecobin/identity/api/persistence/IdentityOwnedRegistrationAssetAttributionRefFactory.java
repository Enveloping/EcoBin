package org.enveloping.ecobin.identity.api.persistence;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;

@Component
public final class IdentityOwnedRegistrationAssetAttributionRefFactory {

    public RegistrationAssetAttributionRef issue(
            long assetKey,
            long registeredUserCount) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()) {
            throw new IllegalStateException(
                    "registration attribution requires its read transaction");
        }
        var result = new RegistrationAssetAttributionRef(
                assetKey,
                registeredUserCount,
                new IdentityHashMap<>(
                        TransactionSynchronizationManager.getResourceMap()));
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
