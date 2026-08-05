package org.enveloping.ecobin.device.api.persistence;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;

@Component
public final class DeviceOwnedBagTraceSessionSelectionRefFactory {

    public BagTraceSessionSelectionRef issue(
            List<BagTraceSessionSelectionRef.Session> sessions) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "bag trace sessions require a transaction");
        }
        var result = new BagTraceSessionSelectionRef(
                sessions, TransactionSynchronizationManager.getResourceMap());
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
