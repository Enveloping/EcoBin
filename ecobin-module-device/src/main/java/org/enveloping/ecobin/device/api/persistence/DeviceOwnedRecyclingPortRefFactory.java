package org.enveloping.ecobin.device.api.persistence;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
public final class DeviceOwnedRecyclingPortRefFactory {

    public RecyclingDevicePortRef issue(
            long tenantKey, long organizationKey, long portKey) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "recycling device port requires a transaction");
        }
        var result = new RecyclingDevicePortRef(
                tenantKey, organizationKey, portKey,
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
