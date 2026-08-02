package org.enveloping.ecobin.framework.reliability;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

/**
 * Opaque, single-use relation between a platform-scoped inbox message and an
 * authoritative write.
 */
public final class TrustedPlatformInboxRef {

    private final long inboxKey;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    TrustedPlatformInboxRef(
            long inboxKey,
            Map<Object, Object> transactionResources) {
        if (inboxKey <= 0) {
            throw new IllegalArgumentException(
                    "inboxKey must be positive");
        }
        this.inboxKey = inboxKey;
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(
                transactionResources);
    }

    public synchronized <T> T use(PlatformInboxFunction<T> function) {
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "trusted inbox reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "trusted inbox reference requires its transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "trusted inbox reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "trusted inbox reference was already consumed");
        }
        consumed = true;
        return function.apply(inboxKey);
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "TrustedPlatformInboxRef[REDACTED]";
    }

    @FunctionalInterface
    public interface PlatformInboxFunction<T> {

        T apply(long inboxKey);
    }
}
