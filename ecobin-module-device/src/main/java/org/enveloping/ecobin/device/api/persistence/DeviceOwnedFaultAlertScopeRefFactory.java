package org.enveloping.ecobin.device.api.persistence;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
public final class DeviceOwnedFaultAlertScopeRefFactory {

    public DeviceFaultAlertScopeRef issue(
            long tenantKey, long organizationKey) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "device fault alert scope requires a transaction");
        }
        var result = new DeviceFaultAlertScopeRef(
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
