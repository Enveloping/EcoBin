package org.enveloping.ecobin.framework.reliability;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
final class TransactionBoundPlatformDeviceAssetTaskRefFactory
        implements PlatformDeviceAssetTaskRefFactory {

    @Override
    public PlatformDeviceAssetTaskRef issue(long assetKey) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "platform asset task reference requires a transaction");
        }
        var resources = TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "platform asset task reference requires a bound resource");
        }
        PlatformDeviceAssetTaskRef reference =
                new PlatformDeviceAssetTaskRef(assetKey, resources);
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
