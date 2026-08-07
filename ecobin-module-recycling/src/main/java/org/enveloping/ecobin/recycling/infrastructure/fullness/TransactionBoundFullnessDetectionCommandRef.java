package org.enveloping.ecobin.recycling.infrastructure.fullness;

import org.enveloping.ecobin.device.api.persistence.FullnessDetectionCommandRef;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;

public final class TransactionBoundFullnessDetectionCommandRef
        implements FullnessDetectionCommandRef {

    private final ForeignKeys keys;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    private TransactionBoundFullnessDetectionCommandRef(
            ForeignKeys keys,
            Map<Object, Object> transactionResources) {
        this.keys = Objects.requireNonNull(keys, "keys");
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(
                Objects.requireNonNull(
                        transactionResources,
                        "transactionResources"));
    }

    public static FullnessDetectionCommandRef issue(
            long tenantKey,
            long organizationKey,
            long assetKey,
            long portKey,
            long detectionKey,
            long deviceConfigVersionKey,
            long portConfigSnapshotKey) {
        requireTransaction();
        var reference =
                new TransactionBoundFullnessDetectionCommandRef(
                        new ForeignKeys(
                                tenantKey,
                                organizationKey,
                                assetKey,
                                portKey,
                                detectionKey,
                                deviceConfigVersionKey,
                                portConfigSnapshotKey),
                        TransactionSynchronizationManager
                                .getResourceMap());
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCompletion(int status) {
                        reference.markTransactionCompleted();
                    }
                });
        return reference;
    }

    @Override
    public synchronized <T> T withForeignKeysOnce(
            Function<ForeignKeys, T> function) {
        Objects.requireNonNull(function, "function");
        requireUsable();
        consumed = true;
        return function.apply(keys);
    }

    private void requireUsable() {
        if (Thread.currentThread().threadId() != ownerThreadId
                || transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "fullness detection reference left its transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction || consumed) {
            throw new IllegalStateException(
                    "fullness detection reference cannot be reused or moved");
        }
    }

    private static void requireTransaction() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || TransactionSynchronizationManager
                .getResourceMap().isEmpty()) {
            throw new IllegalStateException(
                    "fullness detection reference requires a transaction");
        }
    }

    private synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "FullnessDetectionCommandRef[REDACTED]";
    }
}
