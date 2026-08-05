package org.enveloping.ecobin.device.api.persistence;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;

/** Device-owned port relationship consumed only by recycling queries. */
public final class RecyclingDevicePortRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long portKey;
    private final long threadId = Thread.currentThread().threadId();
    private final Map<Object, Object> resources;
    private boolean consumed;
    private boolean completed;

    RecyclingDevicePortRef(
            long tenantKey,
            long organizationKey,
            long portKey,
            Map<Object, Object> resources) {
        this.tenantKey = tenantKey;
        this.organizationKey = organizationKey;
        this.portKey = portKey;
        this.resources = new IdentityHashMap<>(resources);
    }

    public synchronized <T> T consumeOnce(PortFunction<T> function) {
        if (consumed || completed || threadId != Thread.currentThread().threadId()
                || !TransactionSynchronizationManager
                .isActualTransactionActive()
                || resources.entrySet().stream().anyMatch(entry ->
                TransactionSynchronizationManager.getResource(entry.getKey())
                        != entry.getValue())) {
            throw new IllegalStateException(
                    "recycling device port reference is no longer valid");
        }
        consumed = true;
        return function.apply(tenantKey, organizationKey, portKey);
    }

    synchronized void markCompleted() {
        completed = true;
    }

    @Override
    public String toString() {
        return "RecyclingDevicePortRef[REDACTED]";
    }

    @FunctionalInterface
    public interface PortFunction<T> {
        T apply(long tenantKey, long organizationKey, long portKey);
    }
}
