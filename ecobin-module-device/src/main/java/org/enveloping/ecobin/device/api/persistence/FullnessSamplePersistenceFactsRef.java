package org.enveloping.ecobin.device.api.persistence;

import org.enveloping.ecobin.device.api.result.FullnessSamplePersistenceFacts;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;

public final class FullnessSamplePersistenceFactsRef {

    private final FullnessSamplePersistenceFacts facts;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    FullnessSamplePersistenceFactsRef(
            FullnessSamplePersistenceFacts facts,
            Map<Object, Object> transactionResources) {
        this.facts = Objects.requireNonNull(facts, "facts");
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(
                Objects.requireNonNull(
                        transactionResources,
                        "transactionResources"));
    }

    public synchronized <T> T useOnce(
            Function<FullnessSamplePersistenceFacts, T> function) {
        Objects.requireNonNull(function, "function");
        if (Thread.currentThread().threadId() != ownerThreadId
                || transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "fullness sample facts require their issuing transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction || consumed) {
            throw new IllegalStateException(
                    "fullness sample facts cannot be reused or moved");
        }
        consumed = true;
        return function.apply(facts);
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "FullnessSamplePersistenceFactsRef[REDACTED]";
    }
}
