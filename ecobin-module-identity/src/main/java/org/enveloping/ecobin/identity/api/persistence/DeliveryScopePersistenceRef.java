package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;

/**
 * Opaque relationship reference for a delivery query, review or correction.
 *
 * <p>The reference is deliberately non-serializable, exposes no key getters
 * and can only be consumed once in the issuing thread and transaction.</p>
 */
public final class DeliveryScopePersistenceRef {

    private final long tenantKey;
    private final long organizationKey;
    private final Long platformAdminKey;
    private final Long staffAccountKey;
    private final TransactionBoundReferenceGuard guard;

    DeliveryScopePersistenceRef(
            long tenantKey,
            long organizationKey,
            Long platformAdminKey,
            Long staffAccountKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey,
                "organizationKey");
        this.platformAdminKey = nullablePositive(
                platformAdminKey,
                "platformAdminKey");
        this.staffAccountKey = nullablePositive(
                staffAccountKey,
                "staffAccountKey");
        if ((platformAdminKey == null) == (staffAccountKey == null)) {
            throw new IllegalArgumentException(
                    "exactly one delivery actor key is required");
        }
        this.guard = new TransactionBoundReferenceGuard(
                transactionResources);
    }

    public synchronized <T> T withScopeOnce(ScopeFunction<T> function) {
        guard.claimOnce();
        return function.apply(
                tenantKey,
                organizationKey,
                platformAdminKey,
                staffAccountKey);
    }

    synchronized void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "DeliveryScopePersistenceRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    private static Long nullablePositive(Long value, String name) {
        if (value != null && value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface ScopeFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                Long platformAdminKey,
                Long staffAccountKey);
    }
}
