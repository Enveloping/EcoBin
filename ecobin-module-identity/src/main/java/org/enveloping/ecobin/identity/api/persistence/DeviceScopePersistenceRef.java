package org.enveloping.ecobin.identity.api.persistence;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

/**
 * Single-use relationship reference for device writes and scoped reads.
 *
 * <p>The reference is deliberately non-serializable and exposes no key
 * getters. It can only be consumed in the issuing thread and transaction.</p>
 */
public final class DeviceScopePersistenceRef {

    private final Long tenantKey;
    private final Long organizationKey;
    private final Long platformAdminKey;
    private final Long staffAccountKey;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    DeviceScopePersistenceRef(
            Long tenantKey,
            Long organizationKey,
            Long platformAdminKey,
            Long staffAccountKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = nullablePositive(tenantKey, "tenantKey");
        this.organizationKey = nullablePositive(
                organizationKey, "organizationKey");
        this.platformAdminKey = nullablePositive(
                platformAdminKey, "platformAdminKey");
        this.staffAccountKey = nullablePositive(
                staffAccountKey, "staffAccountKey");
        if ((platformAdminKey == null) == (staffAccountKey == null)) {
            throw new IllegalArgumentException(
                    "exactly one device actor key is required");
        }
        if (organizationKey != null && tenantKey == null) {
            throw new IllegalArgumentException(
                    "organizationKey requires tenantKey");
        }
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(
                transactionResources);
    }

    public synchronized void writeForeignKeysTo(ForeignKeyWriter writer) {
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "device scope reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "device scope reference requires its issuing transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "device scope reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "device scope reference was already consumed");
        }
        consumed = true;
        writer.write(
                tenantKey,
                organizationKey,
                platformAdminKey,
                staffAccountKey);
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "DeviceScopePersistenceRef[REDACTED]";
    }

    private static Long nullablePositive(Long value, String name) {
        if (value != null && value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface ForeignKeyWriter {
        void write(
                Long tenantKey,
                Long organizationKey,
                Long platformAdminKey,
                Long staffAccountKey);
    }
}
