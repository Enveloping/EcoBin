package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * 仅供 funds 在 GET 投递或钱包查询的只读事务中定位当前用户钱包。
 *
 * <p>它不能用于资金写入、授权或其他钱包用例；内部键没有 getter，并且只能在
 * 发行它的同一线程、同一只读事务中展开一次。</p>
 */
public final class DeliveryWalletQueryOwnerRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long organizationUserKey;
    private final UUID organizationUserUid;
    private final TransactionBoundReferenceGuard guard;

    DeliveryWalletQueryOwnerRef(
            long tenantKey,
            long organizationKey,
            long organizationUserKey,
            UUID organizationUserUid,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(organizationKey, "organizationKey");
        this.organizationUserKey =
                positive(organizationUserKey, "organizationUserKey");
        this.organizationUserUid =
                Objects.requireNonNull(
                        organizationUserUid,
                        "organizationUserUid");
        this.guard = new TransactionBoundReferenceGuard(
                transactionResources,
                true);
    }

    public <T> T withWalletQualificationOwnerOnce(
            WalletQualificationOwnerFunction<T> function) {
        return withWalletOwnerOnce(function);
    }

    public <T> T withWalletOwnerOnce(
            WalletQualificationOwnerFunction<T> function) {
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
        return "DeliveryWalletQueryOwnerRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface WalletQualificationOwnerFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                long organizationUserKey,
                UUID organizationUserUid);
    }
}
