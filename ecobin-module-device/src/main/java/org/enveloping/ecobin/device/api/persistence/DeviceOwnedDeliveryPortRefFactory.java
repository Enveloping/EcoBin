package org.enveloping.ecobin.device.api.persistence;

import org.enveloping.ecobin.device.application.startdelivery.DeviceDeliveryPortRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
final class DeviceOwnedDeliveryPortRefFactory
        implements DeviceDeliveryPortRefFactory {

    @Override
    public DeviceDeliveryPortRef issue(
            long tenantKey,
            long organizationKey,
            long deploymentKey,
            long portKey) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "device delivery port reference requires a transaction");
        }
        var resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "device delivery port reference requires a bound resource");
        }
        DeviceDeliveryPortRef reference = new DeviceDeliveryPortRef(
                tenantKey,
                organizationKey,
                deploymentKey,
                portKey,
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
