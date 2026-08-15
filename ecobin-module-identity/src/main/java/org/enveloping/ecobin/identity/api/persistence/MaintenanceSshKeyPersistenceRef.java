package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;

/**
 * Transaction-bound, single-use maintenance SSH key relationship reference.
 *
 * <p>The identity-owned database key is never exposed through a getter. A
 * collaborating module can only pass it directly to a foreign-key write on
 * the issuing thread and in the issuing transaction.</p>
 */
public final class MaintenanceSshKeyPersistenceRef {

    private final long maintenanceSshKeyKey;
    private final TransactionBoundReferenceGuard guard;

    MaintenanceSshKeyPersistenceRef(
            long maintenanceSshKeyKey,
            Map<Object, Object> transactionResources) {
        if (maintenanceSshKeyKey <= 0) {
            throw new IllegalArgumentException(
                    "maintenanceSshKeyKey must be positive");
        }
        this.maintenanceSshKeyKey = maintenanceSshKeyKey;
        this.guard = new TransactionBoundReferenceGuard(
                transactionResources);
    }

    public synchronized void writeForeignKeyTo(ForeignKeyWriter writer) {
        if (writer == null) {
            throw new IllegalArgumentException("writer is required");
        }
        guard.claimOnce();
        writer.write(maintenanceSshKeyKey);
    }

    synchronized void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "MaintenanceSshKeyPersistenceRef[REDACTED]";
    }

    @FunctionalInterface
    public interface ForeignKeyWriter {
        void write(long maintenanceSshKeyKey);
    }
}
