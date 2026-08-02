package org.enveloping.ecobin.device.api.persistence;

import org.enveloping.ecobin.device.api.result.FullnessStateChangePersistenceFacts;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.LinkedHashMap;
import java.util.Map;

@Component
public final class DeviceOwnedFullnessStateChangeFactsRefFactory {

    public FullnessStateChangePersistenceFactsRef issue(
            FullnessStateChangePersistenceFacts facts) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "fullness state facts require an active transaction");
        }
        Map<Object, Object> resources = new LinkedHashMap<>();
        TransactionSynchronizationManager.getResourceMap()
                .forEach(resources::put);
        FullnessStateChangePersistenceFactsRef reference =
                new FullnessStateChangePersistenceFactsRef(
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
