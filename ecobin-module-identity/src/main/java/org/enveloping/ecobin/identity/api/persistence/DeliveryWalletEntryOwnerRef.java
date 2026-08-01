package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * identity 签发的钱包明细归属用户可信组合。
 *
 * <p>内部机构用户编号与公开 UUID 在 identity 核对后一次性封装。funds
 * 只能在签发它的同一线程、同一事务中展开一次，不能让调用方分别传入并重新
 * 组合，也不能把内部键写入传输、日志、任务或缓存。</p>
 */
public final class DeliveryWalletEntryOwnerRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long organizationUserKey;
    private final UUID organizationUserUid;
    private final TransactionBoundReferenceGuard guard;

    DeliveryWalletEntryOwnerRef(
            long tenantKey,
            long organizationKey,
            long organizationUserKey,
            UUID organizationUserUid,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey,
                "organizationKey");
        this.organizationUserKey = positive(
                organizationUserKey,
                "organizationUserKey");
        this.organizationUserUid = Objects.requireNonNull(
                organizationUserUid,
                "organizationUserUid");
        this.guard = new TransactionBoundReferenceGuard(
                transactionResources);
    }

    public <T> T withWalletEntryOwnerOnce(
            WalletEntryOwnerFunction<T> function) {
        Objects.requireNonNull(function, "function");
        guard.claimOnce();
        return function.apply(
                tenantKey,
                organizationKey,
                organizationUserKey,
                organizationUserUid);
    }

    void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "DeliveryWalletEntryOwnerRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(
                    name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface WalletEntryOwnerFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                long organizationUserKey,
                UUID organizationUserUid);
    }
}
