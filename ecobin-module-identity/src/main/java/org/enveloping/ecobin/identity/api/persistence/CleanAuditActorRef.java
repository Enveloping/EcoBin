package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;
import java.util.Objects;

/** 当前清运请求写审计时使用的一次性清运员主体引用。 */
public final class CleanAuditActorRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long organizationUserKey;
    private final TransactionBoundReferenceGuard guard;

    CleanAuditActorRef(
            long tenantKey,
            long organizationKey,
            long organizationUserKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey, "organizationKey");
        this.organizationUserKey = positive(
                organizationUserKey, "organizationUserKey");
        this.guard = new TransactionBoundReferenceGuard(
                transactionResources);
    }

    public <T> T withAuditActorOnce(AuditActorFunction<T> function) {
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
        return "CleanAuditActorRef[REDACTED]";
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
