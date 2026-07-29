package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

final class DeliveryOrderTransactionRefGuard {

    private final long ownerThreadId = Thread.currentThread().threadId();
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    private DeliveryOrderTransactionRefGuard(
            Map<Object, Object> resources) {
        transactionResources = new IdentityHashMap<>(resources);
    }

    static DeliveryOrderTransactionRefGuard issue() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "delivery order reference requires an active transaction");
        }
        Map<Object, Object> resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "delivery order reference requires a bound resource");
        }
        DeliveryOrderTransactionRefGuard guard =
                new DeliveryOrderTransactionRefGuard(resources);
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCompletion(int status) {
                        guard.markTransactionCompleted();
                    }
                });
        return guard;
    }

    synchronized void claimOnce() {
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "delivery order reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "delivery order reference requires its transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "delivery order reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "delivery order reference was already consumed");
        }
        consumed = true;
    }

    private synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }
}
