package org.enveloping.ecobin.recycling.application.clean;

import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

final class CleanRecordTransactionRefGuard {

    private final long ownerThreadId = Thread.currentThread().threadId();
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    private CleanRecordTransactionRefGuard(
            Map<Object, Object> resources) {
        transactionResources = new IdentityHashMap<>(resources);
    }

    static CleanRecordTransactionRefGuard issue() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "clean record reference requires an active transaction");
        }
        Map<Object, Object> resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "clean record reference requires a bound resource");
        }
        CleanRecordTransactionRefGuard guard =
                new CleanRecordTransactionRefGuard(resources);
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCompletion(int status) {
                        guard.transactionCompleted = true;
                    }
                });
        return guard;
    }

    synchronized void claimOnce() {
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "clean record reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "clean record reference requires its transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "clean record reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "clean record reference was already consumed");
        }
        consumed = true;
    }
}
