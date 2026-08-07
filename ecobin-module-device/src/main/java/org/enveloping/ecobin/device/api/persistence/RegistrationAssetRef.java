package org.enveloping.ecobin.device.api.persistence;

import org.enveloping.ecobin.identity.api.persistence.OrganizationUserRegistrationAttributionRef;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

/**
 * 首次机构用户注册归因写入所需的永久设备资产引用。
 *
 * <p>引用没有键值 getter，不可序列化，只能在签发它的同线程、同事务中消费一次。</p>
 */
public final class RegistrationAssetRef
        implements OrganizationUserRegistrationAttributionRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long assetKey;
    private final long ownerThreadId;
    private final Map<Object, Object> transactionResources;
    private boolean consumed;
    private boolean transactionCompleted;

    RegistrationAssetRef(
            long tenantKey,
            long organizationKey,
            long assetKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(organizationKey, "organizationKey");
        this.assetKey = positive(assetKey, "assetKey");
        this.ownerThreadId = Thread.currentThread().threadId();
        this.transactionResources = new IdentityHashMap<>(transactionResources);
    }

    @Override
    public synchronized void writeForeignKeyTo(ForeignKeyWriter writer) {
        if (Thread.currentThread().threadId() != ownerThreadId) {
            throw new IllegalStateException(
                    "registration asset reference cannot cross threads");
        }
        if (transactionCompleted
                || !TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "registration asset reference requires its issuing transaction");
        }
        boolean sameTransaction = transactionResources.entrySet().stream()
                .allMatch(entry -> TransactionSynchronizationManager.getResource(
                        entry.getKey()) == entry.getValue());
        if (!sameTransaction) {
            throw new IllegalStateException(
                    "registration asset reference cannot cross transactions");
        }
        if (consumed) {
            throw new IllegalStateException(
                    "registration asset reference was already consumed");
        }
        consumed = true;
        writer.write(tenantKey, organizationKey, assetKey);
    }

    synchronized void markTransactionCompleted() {
        transactionCompleted = true;
    }

    @Override
    public String toString() {
        return "RegistrationAssetRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }
}
