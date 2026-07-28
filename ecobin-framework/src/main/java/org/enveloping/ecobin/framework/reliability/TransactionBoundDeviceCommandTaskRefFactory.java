package org.enveloping.ecobin.framework.reliability;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
final class TransactionBoundDeviceCommandTaskRefFactory
        implements DeviceCommandTaskRefFactory {

    @Override
    public DeviceCommandTaskRef issue(
            long tenantKey,
            long organizationKey,
            long deploymentKey,
            long commandKey) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "device command task reference requires a transaction");
        }
        var resources = TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "device command task reference requires a bound resource");
        }
        DeviceCommandTaskRef reference = new DeviceCommandTaskRef(
                tenantKey,
                organizationKey,
                deploymentKey,
                commandKey,
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
