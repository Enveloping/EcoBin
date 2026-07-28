package org.enveloping.ecobin.framework.reliability;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

/**
 * Opaque, single-use relation between an organization-scoped inbox message
 * and an authoritative business write.
 */
public final class TrustedOrganizationInboxRef {

    private final long inboxKey;
    private final long tenantKey;
    private final long organizationKey;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    TrustedOrganizationInboxRef(
            long inboxKey,
            long tenantKey,
            long organizationKey,
            Map<Object, Object> transactionResources) {
        this.inboxKey = positive(inboxKey, "inboxKey");
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey, "organizationKey");
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(
                transactionResources);
    }

    public synchronized <T> T use(ScopedInboxFunction<T> function) {
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
        return function.apply(inboxKey, tenantKey, organizationKey);
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "TrustedOrganizationInboxRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface ScopedInboxFunction<T> {

        T apply(long inboxKey, long tenantKey, long organizationKey);
    }
}
