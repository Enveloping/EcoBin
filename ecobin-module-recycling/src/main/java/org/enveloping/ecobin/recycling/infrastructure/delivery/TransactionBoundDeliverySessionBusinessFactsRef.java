package org.enveloping.ecobin.recycling.infrastructure.delivery;

import org.enveloping.ecobin.device.api.persistence.DeliverySessionBusinessFactsRef;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;

/**
 * Recycling-owned implementation of the relation needed to create the
 * device-owned delivery session.
 */
final class TransactionBoundDeliverySessionBusinessFactsRef
        implements DeliverySessionBusinessFactsRef {

    private final BusinessForeignKeys keys;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    private TransactionBoundDeliverySessionBusinessFactsRef(
            BusinessForeignKeys keys,
            Map<Object, Object> transactionResources) {
        this.keys = Objects.requireNonNull(keys, "keys");
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(
                Objects.requireNonNull(
                        transactionResources,
                        "transactionResources"));
    }

    static TransactionBoundDeliverySessionBusinessFactsRef issue(
            long tenantKey,
            long organizationKey,
            long deliveryConfigurationKey,
            long bagKey) {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "delivery business reference requires a transaction");
        }
        var resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "delivery business reference requires a bound resource");
        }
        var reference =
                new TransactionBoundDeliverySessionBusinessFactsRef(
                        new BusinessForeignKeys(
                                tenantKey,
                                organizationKey,
                                deliveryConfigurationKey,
                                bagKey),
                        resources);
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
    public synchronized <T> T withBusinessForeignKeysOnce(
            Function<BusinessForeignKeys, T> function) {
        Objects.requireNonNull(function, "function");
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "delivery business reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "delivery business reference requires its transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "delivery business reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "delivery business reference was already consumed");
        }
        consumed = true;
        return function.apply(keys);
    }

    private synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "DeliverySessionBusinessFactsRef[REDACTED]";
    }
}
