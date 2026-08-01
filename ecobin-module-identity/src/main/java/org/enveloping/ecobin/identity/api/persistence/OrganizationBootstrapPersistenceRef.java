package org.enveloping.ecobin.identity.api.persistence;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

/**
 * 仅供新机构同线程、同事务初始化业务模块复合外键使用。
 *
 * <p>不提供键值 getter，不实现 Serializable，且只能展开一次。</p>
 */
public final class OrganizationBootstrapPersistenceRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    OrganizationBootstrapPersistenceRef(
            long tenantKey,
            long organizationKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey, "organizationKey");
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(
                transactionResources);
    }

    public synchronized void writeForeignKeyTo(ForeignKeyWriter writer) {
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "organization bootstrap reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "organization bootstrap reference requires its issuing transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry ->
                        TransactionSynchronizationManager.getResource(
                                entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "organization bootstrap reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "organization bootstrap reference was already consumed");
        }
        consumed = true;
        writer.write(tenantKey, organizationKey);
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "OrganizationBootstrapPersistenceRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface ForeignKeyWriter {
        void write(long tenantKey, long organizationKey);
    }
}
