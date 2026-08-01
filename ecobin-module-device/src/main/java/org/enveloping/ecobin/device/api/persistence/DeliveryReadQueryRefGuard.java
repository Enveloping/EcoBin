package org.enveloping.ecobin.device.api.persistence;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

final class DeliveryReadQueryRefGuard {

    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    DeliveryReadQueryRefGuard(Map<Object, Object> transactionResources) {
        if (transactionResources == null || transactionResources.isEmpty()) {
            throw new IllegalArgumentException(
                    "transactionResources must not be empty");
        }
        ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources =
                new IdentityHashMap<>(transactionResources);
    }

    synchronized void claimOnce() {
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "delivery query reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()) {
            throw new IllegalStateException(
                    "delivery query reference requires its issuing "
                            + "read-only transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "delivery query reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "delivery query reference was already consumed");
        }
        consumed = true;
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }
}
