package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * Transaction-bound organization scope for funds-owned wallet reads.
 *
 * <p>An optional organization user is resolved by identity before the
 * reference is issued. Internal keys are never exposed through getters,
 * transport objects, logs, or cursors.</p>
 */
public final class WalletQueryScopeRef {

    private final long tenantKey;
    private final long organizationKey;
    private final Long organizationUserKey;
    private final UUID organizationUserUid;
    private final TransactionBoundReferenceGuard guard;

    WalletQueryScopeRef(
            long tenantKey,
            long organizationKey,
            Long organizationUserKey,
            UUID organizationUserUid,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey,
                "organizationKey");
        if ((organizationUserKey == null)
                != (organizationUserUid == null)) {
            throw new IllegalArgumentException(
                    "organization user key and uid must both be present "
                            + "or absent");
        }
        this.organizationUserKey = organizationUserKey == null
                ? null
                : positive(organizationUserKey, "organizationUserKey");
        this.organizationUserUid = organizationUserUid;
        this.guard = new TransactionBoundReferenceGuard(
                transactionResources,
                true);
    }

    public <T> T withWalletScopeOnce(WalletScopeFunction<T> function) {
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
        return "WalletQueryScopeRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface WalletScopeFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                Long organizationUserKey,
                UUID organizationUserUid);
    }
}
