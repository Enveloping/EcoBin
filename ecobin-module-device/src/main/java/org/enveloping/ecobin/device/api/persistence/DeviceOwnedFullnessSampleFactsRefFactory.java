package org.enveloping.ecobin.device.api.persistence;

import org.enveloping.ecobin.device.api.result.FullnessSamplePersistenceFacts;
import org.enveloping.ecobin.device.application.fullness.FullnessSampleFactsRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Component
final class DeviceOwnedFullnessSampleFactsRefFactory
        implements FullnessSampleFactsRefFactory {

    @Override
    public FullnessSamplePersistenceFactsRef issue(
            FullnessSamplePersistenceFacts facts) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "fullness sample facts require a transaction");
        }
        var resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "fullness sample facts require a bound resource");
        }
        FullnessSamplePersistenceFactsRef reference =
                new FullnessSamplePersistenceFactsRef(
                        facts,
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
