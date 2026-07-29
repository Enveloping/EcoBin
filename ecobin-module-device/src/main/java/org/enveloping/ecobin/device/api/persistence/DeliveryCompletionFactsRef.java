package org.enveloping.ecobin.device.api.persistence;

import org.enveloping.ecobin.device.api.result.DeliveryCompletionPersistenceFacts;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;

/**
 * Single-use relation from a trusted device physical result to the recycling
 * order projection written in the same transaction.
 */
public final class DeliveryCompletionFactsRef {

    private final DeliveryCompletionPersistenceFacts facts;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    DeliveryCompletionFactsRef(
            DeliveryCompletionPersistenceFacts facts,
            Map<Object, Object> transactionResources) {
        this.facts = Objects.requireNonNull(facts, "facts");
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(
                Objects.requireNonNull(
                        transactionResources,
                        "transactionResources"));
    }

    public synchronized <T> T useOnce(
            Function<DeliveryCompletionPersistenceFacts, T> function) {
        Objects.requireNonNull(function, "function");
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "delivery completion reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "delivery completion reference requires its transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "delivery completion reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "delivery completion reference was already consumed");
        }
        consumed = true;
        return function.apply(facts);
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "DeliveryCompletionFactsRef[REDACTED]";
    }
}
