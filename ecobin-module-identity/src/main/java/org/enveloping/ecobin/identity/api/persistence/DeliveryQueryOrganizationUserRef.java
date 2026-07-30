package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * 仅供投递选项、会话或当前用户投递订单 GET 查询定位机构用户。
 *
 * <p>它不能用于写入或开始授权；内部键没有 getter，并且只能在发行它的
 * 同一线程、同一只读事务中展开一次。</p>
 */
public final class DeliveryQueryOrganizationUserRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long organizationUserKey;
    private final UUID organizationUserUid;
    private final TransactionBoundReferenceGuard guard;

    DeliveryQueryOrganizationUserRef(
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

    public <T> T withDeliveryQueryUserOnce(
            DeliveryQueryUserFunction<T> function) {
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
        return "DeliveryQueryOrganizationUserRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface DeliveryQueryUserFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                long organizationUserKey,
                UUID organizationUserUid);
    }
}
