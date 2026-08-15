package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;

/**
 * Transaction-bound, single-use reference to a factory operator row.
 * The internal primary key is never exposed as a value.
 */
public final class FactoryOperatorPersistenceRef {

    private final long factoryOperatorKey;
    private final TransactionBoundReferenceGuard guard;

    FactoryOperatorPersistenceRef(
            long factoryOperatorKey,
            Map<Object, Object> transactionResources) {
        if (factoryOperatorKey <= 0) {
            throw new IllegalArgumentException(
                    "factoryOperatorKey must be positive");
        }
        this.factoryOperatorKey = factoryOperatorKey;
        this.guard = new TransactionBoundReferenceGuard(
                transactionResources);
    }

    public synchronized void writeForeignKeyTo(ForeignKeyWriter writer) {
        if (writer == null) {
            throw new IllegalArgumentException("writer is required");
        }
        guard.claimOnce();
        writer.write(factoryOperatorKey);
    }

    synchronized void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "FactoryOperatorPersistenceRef[REDACTED]";
    }

    @FunctionalInterface
    public interface ForeignKeyWriter {
        void write(long factoryOperatorKey);
    }
}
