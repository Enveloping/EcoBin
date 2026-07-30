package org.enveloping.ecobin.device.api.persistence;

import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Device-owned, single-use query scope for filtering Recycling delivery
 * orders by a resolved deployment, one port within that deployment, or all
 * ports with the requested number inside an organization.
 */
public final class DeliveryOrderDeviceFilterRef {

    private final long tenantKey;
    private final long organizationKey;
    private final Long deploymentKey;
    private final List<Long> portKeys;
    private final DeliveryReadQueryRefGuard guard;

    DeliveryOrderDeviceFilterRef(
            long tenantKey,
            long organizationKey,
            Long deploymentKey,
            List<Long> portKeys,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey,
                "organizationKey");
        this.deploymentKey = nullablePositive(
                deploymentKey,
                "deploymentKey");
        this.portKeys = List.copyOf(
                Objects.requireNonNull(portKeys, "portKeys"));
        if (deploymentKey == null && this.portKeys.isEmpty()) {
            throw new IllegalArgumentException(
                    "deploymentKey or portKeys is required");
        }
        for (Long portKey : this.portKeys) {
            nullablePositive(
                    Objects.requireNonNull(
                            portKey,
                            "portKey"),
                    "portKey");
        }
        if (this.portKeys.stream().distinct().count()
                != this.portKeys.size()) {
            throw new IllegalArgumentException(
                    "portKeys must be unique");
        }
        guard = new DeliveryReadQueryRefGuard(transactionResources);
    }

    public <T> T withFilterKeysOnce(FilterFunction<T> function) {
        Objects.requireNonNull(function, "function");
        guard.claimOnce();
        return function.apply(
                tenantKey,
                organizationKey,
                deploymentKey,
                portKeys);
    }

    void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "DeliveryOrderDeviceFilterRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(
                    name + " must be positive");
        }
        return value;
    }

    private static Long nullablePositive(Long value, String name) {
        if (value != null && value <= 0) {
            throw new IllegalArgumentException(
                    name + " must be positive");
        }
        return value;
    }

    @FunctionalInterface
    public interface FilterFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                Long deploymentKey,
                List<Long> portKeys);
    }
}
