package org.enveloping.ecobin.device.api.persistence;

import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * 供 recycling 在同一只读事务中查询投递配置及各投口业务事实。
 */
public final class DeliveryOptionsBusinessQueryRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long deploymentKey;
    private final List<PortKey> ports;
    private final DeliveryReadQueryRefGuard guard;

    DeliveryOptionsBusinessQueryRef(
            long tenantKey,
            long organizationKey,
            long deploymentKey,
            List<PortKey> ports,
            Map<Object, Object> transactionResources) {
        this.tenantKey = positive(tenantKey, "tenantKey");
        this.organizationKey = positive(
                organizationKey,
                "organizationKey");
        this.deploymentKey = positive(deploymentKey, "deploymentKey");
        this.ports = List.copyOf(ports);
        if (this.ports.isEmpty()) {
            throw new IllegalArgumentException("ports must not be empty");
        }
        this.guard = new DeliveryReadQueryRefGuard(transactionResources);
    }

    public <T> T withOptionsBusinessKeysOnce(
            OptionsBusinessFunction<T> function) {
        Objects.requireNonNull(function, "function");
        guard.claimOnce();
        return function.apply(
                tenantKey,
                organizationKey,
                deploymentKey,
                ports);
    }

    void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "DeliveryOptionsBusinessQueryRef[REDACTED]";
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    public record PortKey(long portKey, int portNo) {

        public PortKey {
            positive(portKey, "portKey");
            if (portNo < 1 || portNo > 6) {
                throw new IllegalArgumentException(
                        "portNo must be between 1 and 6");
            }
        }
    }

    @FunctionalInterface
    public interface OptionsBusinessFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                long deploymentKey,
                List<PortKey> ports);
    }
}
