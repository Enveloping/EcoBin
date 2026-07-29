package org.enveloping.ecobin.identity.api.persistence;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

/**
 * identity 发行的当前事务关系引用所共享的生命周期守卫。
 */
final class TransactionBoundReferenceGuard {

    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private final boolean readOnlyRequired;
    private boolean consumed;
    private boolean transactionCompleted;

    TransactionBoundReferenceGuard(Map<Object, Object> transactionResources) {
        this(transactionResources, false);
    }

    TransactionBoundReferenceGuard(
            Map<Object, Object> transactionResources,
            boolean readOnlyRequired) {
        if (transactionResources == null || transactionResources.isEmpty()) {
            throw new IllegalArgumentException(
                    "transactionResources must not be empty");
        }
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources =
                new IdentityHashMap<>(transactionResources);
        this.readOnlyRequired = readOnlyRequired;
    }

    synchronized void claimOnce() {
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "persistence reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "persistence reference requires its issuing transaction");
        }
        if (readOnlyRequired
                && !TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()) {
            throw new IllegalStateException(
                    "query reference requires its issuing "
                            + "read-only transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "persistence reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "persistence reference was already consumed");
        }
        consumed = true;
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }
}
