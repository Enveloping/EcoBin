package org.enveloping.ecobin.framework.reliability;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

/**
 * Opaque, transaction-bound relationship from a device command to a reliable
 * task. Internal database keys never leave the callback.
 */
public final class DeviceCommandTaskRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long assetKey;
    private final long commandKey;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    DeviceCommandTaskRef(
            long tenantKey,
            long organizationKey,
            long assetKey,
            long commandKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(organizationKey, "organizationKey");
        this.assetKey = positive(assetKey, "assetKey");
        this.commandKey = positive(commandKey, "commandKey");
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(
                transactionResources);
    }

    public synchronized void writeForeignKeysTo(ForeignKeyWriter writer) {
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "device command task reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "device command task reference requires its transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "device command task reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "device command task reference was already consumed");
        }
        consumed = true;
        writer.write(
                tenantKey,
                organizationKey,
                assetKey,
                commandKey);
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "DeviceCommandTaskRef[REDACTED]";
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
                long assetKey,
                long commandKey);
    }
}
