package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;
import java.util.Objects;

/** 当前清运事务使用的一次性机构关系引用。 */
public final class CleanOrganizationScopeRef {

    private final long tenantKey;
    private final long organizationKey;
    private final TransactionBoundReferenceGuard guard;

    CleanOrganizationScopeRef(
            long tenantKey,
            long organizationKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey, "organizationKey");
        this.guard = new TransactionBoundReferenceGuard(
                transactionResources);
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
        return "CleanOrganizationScopeRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(
                    name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface OrganizationScopeFunction<T> {
        T apply(long tenantKey, long organizationKey);
    }
}
