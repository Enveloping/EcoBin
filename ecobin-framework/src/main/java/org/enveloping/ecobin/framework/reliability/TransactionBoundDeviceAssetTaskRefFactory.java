package org.enveloping.ecobin.framework.reliability;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
final class TransactionBoundDeviceAssetTaskRefFactory
        implements DeviceAssetTaskRefFactory {

    @Override
    public DeviceAssetTaskRef issue(
            long tenantKey,
            long organizationKey,
            long assetKey) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "device asset task reference requires a transaction");
        }
        var resources = TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "device asset task reference requires a bound resource");
        }
        DeviceAssetTaskRef reference = new DeviceAssetTaskRef(
                tenantKey, organizationKey, assetKey, resources);
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
