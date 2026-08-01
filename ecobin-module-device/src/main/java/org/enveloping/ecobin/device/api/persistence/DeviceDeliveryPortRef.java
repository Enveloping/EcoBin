package org.enveloping.ecobin.device.api.persistence;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;
import java.util.Objects;

/**
 * Opaque, transaction-bound reference allowing recycling to lock the business
 * facts for one already-locked device port without exposing database keys.
 */
public final class DeviceDeliveryPortRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long deploymentKey;
    private final long portKey;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    DeviceDeliveryPortRef(
            long tenantKey,
            long organizationKey,
            long deploymentKey,
            long portKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey,
                "organizationKey");
        this.deploymentKey = positive(
                deploymentKey,
                "deploymentKey");
        this.portKey = positive(portKey, "portKey");
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(
                Objects.requireNonNull(
                        transactionResources,
                        "transactionResources"));
    }

    public synchronized <T> T useOnce(PortFunction<T> function) {
        Objects.requireNonNull(function, "function");
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "device delivery port reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "device delivery port reference requires its transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "device delivery port reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "device delivery port reference was already consumed");
        }
        consumed = true;
        return function.apply(
                tenantKey,
                organizationKey,
                deploymentKey,
                portKey);
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "DeviceDeliveryPortRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface PortFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                long deploymentKey,
                long portKey);
    }
}
