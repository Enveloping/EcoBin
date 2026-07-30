package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFactsRef;

import java.util.Objects;
import java.util.function.Function;

final class TransactionBoundDeliveryOrderDeviceFactsRef
        implements DeliveryOrderDeviceFactsRef {

    private final BatchKeys keys;
    private final DeliveryOrderTransactionRefGuard guard;

    private TransactionBoundDeliveryOrderDeviceFactsRef(
            BatchKeys keys) {
        this.keys = Objects.requireNonNull(keys, "keys");
        guard = DeliveryOrderTransactionRefGuard.issue();
    }

    static TransactionBoundDeliveryOrderDeviceFactsRef issue(
            BatchKeys keys) {
        return new TransactionBoundDeliveryOrderDeviceFactsRef(keys);
    }

    @Override
    public <T> T withFactKeysOnce(
            Function<BatchKeys, T> function) {
        Objects.requireNonNull(function, "function");
        guard.claimOnce();
        return function.apply(keys);
    }

    @Override
    public String toString() {
        return "DeliveryOrderDeviceFactsRef[REDACTED]";
    }
}
