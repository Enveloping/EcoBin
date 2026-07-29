package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;
import java.util.Objects;

/**
 * 仅供 device 在开始投递复合事务中建立会话的用户复合外键。
 *
 * <p>内部键不提供 getter；调用方只能在发行线程和发行事务内展开一次。</p>
 */
public final class DeliverySessionOrganizationUserRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long organizationUserKey;
    private final TransactionBoundReferenceGuard guard;

    DeliverySessionOrganizationUserRef(
            long tenantKey,
            long organizationKey,
            long organizationUserKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(organizationKey, "organizationKey");
        this.organizationUserKey =
                positive(organizationUserKey, "organizationUserKey");
        this.guard = new TransactionBoundReferenceGuard(transactionResources);
    }

    public <T> T withDeliverySessionUserOnce(
            DeliverySessionUserFunction<T> function) {
        Objects.requireNonNull(function, "function");
        guard.claimOnce();
        return function.apply(
                tenantKey,
                organizationKey,
                organizationUserKey);
    }

    void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "DeliverySessionOrganizationUserRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface DeliverySessionUserFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                long organizationUserKey);
    }
}
