package org.enveloping.ecobin.framework.reliability;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

/**
 * Opaque, transaction-bound relationship from a device deployment to a
 * protocol-control task. Internal database keys never leave the callback.
 */
public final class DeviceDeploymentTaskRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long deploymentKey;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    DeviceDeploymentTaskRef(
            long tenantKey,
            long organizationKey,
            long deploymentKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey, "organizationKey");
        this.deploymentKey = positive(deploymentKey, "deploymentKey");
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(
                transactionResources);
    }

    public synchronized void writeForeignKeysTo(ForeignKeyWriter writer) {
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "device deployment task reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "device deployment task reference requires its transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "device deployment task reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "device deployment task reference was already consumed");
        }
        consumed = true;
        writer.write(tenantKey, organizationKey, deploymentKey);
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "DeviceDeploymentTaskRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface ForeignKeyWriter {
        void write(
                long tenantKey,
                long organizationKey,
                long deploymentKey);
    }
}
