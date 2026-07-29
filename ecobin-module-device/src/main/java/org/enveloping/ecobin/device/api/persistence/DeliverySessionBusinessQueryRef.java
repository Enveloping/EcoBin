package org.enveloping.ecobin.device.api.persistence;

import java.util.Map;
import java.util.Objects;

/**
 * 供 recycling 在同一只读事务中查询某投递会话对应订单。
 */
public final class DeliverySessionBusinessQueryRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long deliverySessionKey;
    private final DeliveryReadQueryRefGuard guard;

    DeliverySessionBusinessQueryRef(
            long tenantKey,
            long organizationKey,
            long deliverySessionKey,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey,
                "organizationKey");
        this.deliverySessionKey = positive(
                deliverySessionKey,
                "deliverySessionKey");
        guard = new DeliveryReadQueryRefGuard(transactionResources);
    }

    public <T> T withSessionBusinessKeysOnce(
            SessionBusinessFunction<T> function) {
        Objects.requireNonNull(function, "function");
        guard.claimOnce();
        return function.apply(
                tenantKey,
                organizationKey,
                deliverySessionKey);
    }

    void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "DeliverySessionBusinessQueryRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface SessionBusinessFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                long deliverySessionKey);
    }
}
