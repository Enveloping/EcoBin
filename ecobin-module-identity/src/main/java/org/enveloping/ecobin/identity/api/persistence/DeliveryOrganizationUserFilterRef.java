package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;

/**
 * Identity-owned organization-user filter relationship.
 *
 * <p>This reference is opaque, non-serializable and can only be consumed once
 * in its issuing read-only transaction and thread.</p>
 */
public final class DeliveryOrganizationUserFilterRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long organizationUserKey;
    private final TransactionBoundReferenceGuard guard;

    DeliveryOrganizationUserFilterRef(
            long tenantKey,
            long organizationKey,
            long organizationUserKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey,
                "organizationKey");
        this.organizationUserKey = positive(
                organizationUserKey,
                "organizationUserKey");
        this.guard = new TransactionBoundReferenceGuard(
                transactionResources,
                true);
    }

    public synchronized <T> T withOrganizationUserOnce(
            FilterFunction<T> function) {
        guard.claimOnce();
        return function.apply(
                tenantKey,
                organizationKey,
                organizationUserKey);
    }

    synchronized void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "DeliveryOrganizationUserFilterRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface FilterFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                long organizationUserKey);
    }
}
