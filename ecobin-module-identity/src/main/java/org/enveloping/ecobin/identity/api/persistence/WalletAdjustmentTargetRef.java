package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/** Identity-verified wallet owner and actor keys for one adjustment command. */
public final class WalletAdjustmentTargetRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long organizationUserKey;
    private final UUID organizationUserUid;
    private final Long platformAdminKey;
    private final Long staffAccountKey;
    private final TransactionBoundReferenceGuard guard;

    WalletAdjustmentTargetRef(
            long tenantKey,
            long organizationKey,
            long organizationUserKey,
            UUID organizationUserUid,
            Long platformAdminKey,
            Long staffAccountKey,
            Map<Object, Object> resources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(organizationKey, "organizationKey");
        this.organizationUserKey = positive(
                organizationUserKey, "organizationUserKey");
        this.organizationUserUid = Objects.requireNonNull(
                organizationUserUid, "organizationUserUid");
        this.platformAdminKey = nullablePositive(
                platformAdminKey, "platformAdminKey");
        this.staffAccountKey = nullablePositive(
                staffAccountKey, "staffAccountKey");
        if ((platformAdminKey == null) == (staffAccountKey == null)) {
            throw new IllegalArgumentException(
                    "exactly one wallet adjustment actor is required");
        }
        this.guard = new TransactionBoundReferenceGuard(resources);
    }

    public <T> T withTargetOnce(TargetFunction<T> function) {
        Objects.requireNonNull(function, "function");
        guard.claimOnce();
        return function.apply(
                tenantKey, organizationKey,
                organizationUserKey, organizationUserUid,
                platformAdminKey, staffAccountKey);
    }

    void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "WalletAdjustmentTargetRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) throw new IllegalArgumentException(name + " must be positive");
        return value;
    }

    private static Long nullablePositive(Long value, String name) {
        if (value != null && value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface TargetFunction<T> {
        T apply(
                long tenantKey,
                long organizationKey,
                long organizationUserKey,
                UUID organizationUserUid,
                Long platformAdminKey,
                Long staffAccountKey);
    }
}
