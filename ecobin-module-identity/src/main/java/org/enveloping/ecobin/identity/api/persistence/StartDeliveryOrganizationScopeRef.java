package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;
import java.util.Objects;

/**
 * 仅供 recycling 在开始投递复合事务中定位机构配置锁根。
 *
 * <p>内部键不提供 getter；调用方只能在发行线程和发行事务内展开一次。</p>
 */
public final class StartDeliveryOrganizationScopeRef {

    private final long tenantKey;
    private final long organizationKey;
    private final TransactionBoundReferenceGuard guard;

    StartDeliveryOrganizationScopeRef(
            long tenantKey,
            long organizationKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(organizationKey, "organizationKey");
        this.guard = new TransactionBoundReferenceGuard(transactionResources);
    }

    public <T> T withOrganizationScopeOnce(
            OrganizationScopeFunction<T> function) {
        Objects.requireNonNull(function, "function");
        guard.claimOnce();
        return function.apply(tenantKey, organizationKey);
    }

    void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "StartDeliveryOrganizationScopeRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface OrganizationScopeFunction<T> {

        T apply(long tenantKey, long organizationKey);
    }
}
