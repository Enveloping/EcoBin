package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.funds.api.persistence.DeliveryRevisionWalletEntryRef;

import java.util.Objects;
import java.util.function.Function;

final class TransactionBoundDeliveryRevisionWalletEntryRef
        implements DeliveryRevisionWalletEntryRef {

    private final WalletEntryForeignKeys keys;
    private final DeliveryOrderTransactionRefGuard guard;

    private TransactionBoundDeliveryRevisionWalletEntryRef(
            WalletEntryForeignKeys keys) {
        this.keys = Objects.requireNonNull(keys, "keys");
        guard = DeliveryOrderTransactionRefGuard.issue();
    }

    static TransactionBoundDeliveryRevisionWalletEntryRef issue(
            long tenantId,
            long organizationId,
            long organizationUserId,
            long deliveryRevisionId) {
        return new TransactionBoundDeliveryRevisionWalletEntryRef(
                new WalletEntryForeignKeys(
                        tenantId,
                        organizationId,
                        organizationUserId,
                        deliveryRevisionId));
    }

    @Override
    public <T> T withWalletEntryForeignKeysOnce(
            Function<WalletEntryForeignKeys, T> function) {
        Objects.requireNonNull(function, "function");
        guard.claimOnce();
        return function.apply(keys);
    }

    @Override
    public String toString() {
        return "DeliveryRevisionWalletEntryRef[REDACTED]";
    }
}
