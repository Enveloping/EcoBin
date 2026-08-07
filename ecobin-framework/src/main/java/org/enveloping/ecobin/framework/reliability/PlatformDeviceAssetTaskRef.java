package org.enveloping.ecobin.framework.reliability;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

/**
 * Opaque transaction-bound reference from a platform-owned device asset to a
 * platform-scoped reliable control task.
 *
 * <p>The asset may not have a tenant or organization yet.  This is therefore
 * deliberately separate from {@link DeviceAssetTaskRef}; using the
 * organization-scoped reference would invent ownership that does not exist
 * during factory acceptance.</p>
 */
public final class PlatformDeviceAssetTaskRef {

    private final long assetKey;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    PlatformDeviceAssetTaskRef(
            long assetKey,
            Map<Object, Object> transactionResources) {
        if (assetKey <= 0) {
            throw new IllegalArgumentException("assetKey must be positive");
        }
        this.assetKey = assetKey;
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(
                transactionResources);
    }

    public synchronized void writeForeignKeyTo(ForeignKeyWriter writer) {
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "platform asset task reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "platform asset task reference requires its transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry -> TransactionSynchronizationManager
                        .getResource(entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "platform asset task reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "platform asset task reference was already consumed");
        }
        consumed = true;
        writer.write(assetKey);
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "PlatformDeviceAssetTaskRef[REDACTED]";
    }

    @FunctionalInterface
    public interface ForeignKeyWriter {
        void write(long assetKey);
    }
}
