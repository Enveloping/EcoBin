package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletEntryOwnerRequestRef;

import java.util.Objects;

final class TransactionBoundDeliveryWalletEntryOwnerRequestRef
        implements DeliveryWalletEntryOwnerRequestRef {

    private final long tenantId;
    private final long organizationId;
    private final long organizationUserId;
    private final DeliveryOrderTransactionRefGuard guard;

    private TransactionBoundDeliveryWalletEntryOwnerRequestRef(
            long tenantId,
            long organizationId,
            long organizationUserId) {
        this.tenantId = positive(tenantId, "tenantId");
        this.organizationId = positive(
                organizationId,
                "organizationId");
        this.organizationUserId = positive(
                organizationUserId,
                "organizationUserId");
        this.guard = DeliveryOrderTransactionRefGuard.issue();
    }

    static TransactionBoundDeliveryWalletEntryOwnerRequestRef issue(
            long tenantId,
            long organizationId,
            long organizationUserId) {
        return new TransactionBoundDeliveryWalletEntryOwnerRequestRef(
                tenantId,
                organizationId,
                organizationUserId);
    }

    @Override
    public <T> T withRequestedOwnerOnce(
            RequestedOwnerFunction<T> function) {
        Objects.requireNonNull(function, "function");
        guard.claimOnce();
        return function.apply(
                tenantId,
                organizationId,
                organizationUserId);
    }

    @Override
    public String toString() {
        return "DeliveryWalletEntryOwnerRequestRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(
                    name + " must be positive");
        }
        return value;
    }
}
