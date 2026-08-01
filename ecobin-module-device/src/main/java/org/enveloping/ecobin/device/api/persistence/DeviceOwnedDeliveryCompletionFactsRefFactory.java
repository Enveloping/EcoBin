package org.enveloping.ecobin.device.api.persistence;

import org.enveloping.ecobin.device.api.result.DeliveryCompletionPersistenceFacts;
import org.enveloping.ecobin.device.application.delivery.DeliveryCompletionFactsRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
final class DeviceOwnedDeliveryCompletionFactsRefFactory
        implements DeliveryCompletionFactsRefFactory {

    @Override
    public DeliveryCompletionFactsRef issue(
            DeliveryCompletionPersistenceFacts facts) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "delivery completion reference requires a transaction");
        }
        var resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "delivery completion reference requires a bound resource");
        }
        DeliveryCompletionFactsRef reference =
                new DeliveryCompletionFactsRef(facts, resources);
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
