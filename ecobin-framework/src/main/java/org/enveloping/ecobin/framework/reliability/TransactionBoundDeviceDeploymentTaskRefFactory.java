package org.enveloping.ecobin.framework.reliability;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
final class TransactionBoundDeviceDeploymentTaskRefFactory
        implements DeviceDeploymentTaskRefFactory {

    @Override
    public DeviceDeploymentTaskRef issue(
            long tenantKey,
            long organizationKey,
            long deploymentKey) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "device deployment task reference requires a transaction");
        }
        var resources = TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "device deployment task reference requires a bound resource");
        }
        DeviceDeploymentTaskRef reference =
                new DeviceDeploymentTaskRef(
                        tenantKey,
                        organizationKey,
                        deploymentKey,
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
