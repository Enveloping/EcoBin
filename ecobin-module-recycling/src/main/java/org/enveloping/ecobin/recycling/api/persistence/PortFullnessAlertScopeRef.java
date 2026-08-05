package org.enveloping.ecobin.recycling.api.persistence;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;
import java.util.function.BiFunction;

/** Single-use recycling-to-operations relationship reference for one alert. */
public final class PortFullnessAlertScopeRef {

    private final long tenantKey;
    private final long organizationKey;
    private final long threadId = Thread.currentThread().threadId();
    private final Map<Object, Object> resources;
    private boolean consumed;
    private boolean completed;

    PortFullnessAlertScopeRef(
            long tenantKey,
            long organizationKey,
            Map<Object, Object> resources) {
        this.tenantKey = tenantKey;
        this.organizationKey = organizationKey;
        this.resources = new IdentityHashMap<>(resources);
    }

    public synchronized <T> T withScopeOnce(
            BiFunction<Long, Long, T> function) {
        if (consumed || completed || threadId != Thread.currentThread().threadId()
                || !TransactionSynchronizationManager
                .isActualTransactionActive()
                || resources.entrySet().stream().anyMatch(entry ->
                TransactionSynchronizationManager.getResource(entry.getKey())
                        != entry.getValue())) {
            throw new IllegalStateException(
                    "port fullness alert scope is no longer valid");
        }
        consumed = true;
        return function.apply(tenantKey, organizationKey);
    }

    synchronized void markCompleted() {
        completed = true;
    }

    @Override
    public String toString() {
        return "PortFullnessAlertScopeRef[REDACTED]";
    }
}
