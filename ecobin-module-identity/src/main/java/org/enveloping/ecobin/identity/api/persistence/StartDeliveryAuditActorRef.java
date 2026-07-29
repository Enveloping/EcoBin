package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;
import java.util.Objects;

/**
 * One-time relation used to append the authenticated miniapp user's audit
 * fact in the same start-delivery transaction.
 */
public final class StartDeliveryAuditActorRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long organizationUserKey;
    private final TransactionBoundReferenceGuard guard;

    StartDeliveryAuditActorRef(
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
                transactionResources);
    }

    public <T> T withAuditActorOnce(
            AuditActorFunction<T> function) {
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
        return "StartDeliveryAuditActorRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(
                    name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface AuditActorFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                long organizationUserKey);
    }
}
