package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;
import java.util.Objects;

/** Writable-transaction scope used only to lock the current delivery config. */
public final class WalletAdjustmentScopeRef {

    private final long tenantKey;
    private final long organizationKey;
    private final TransactionBoundReferenceGuard guard;

    WalletAdjustmentScopeRef(
            long tenantKey,
            long organizationKey,
            Map<Object, Object> resources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(organizationKey, "organizationKey");
        this.guard = new TransactionBoundReferenceGuard(resources);
    }

    public <T> T withScopeOnce(ScopeFunction<T> function) {
        Objects.requireNonNull(function, "function");
        guard.claimOnce();
        return function.apply(tenantKey, organizationKey);
    }

    void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "WalletAdjustmentScopeRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) throw new IllegalArgumentException(name + " must be positive");
        return value;
    }

    @FunctionalInterface
    public interface ScopeFunction<T> {
        T apply(long tenantKey, long organizationKey);
    }
}
