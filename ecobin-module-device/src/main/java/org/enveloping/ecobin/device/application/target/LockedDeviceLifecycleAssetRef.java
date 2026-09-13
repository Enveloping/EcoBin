package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.persistence.DeviceLifecycleAssetRef;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.IdentityHashMap;
import java.util.Map;
import java.util.function.LongConsumer;

/** Only the asset owner can issue this relation, while retaining the asset lock. */
final class LockedDeviceLifecycleAssetRef implements DeviceLifecycleAssetRef {
    private final long assetId;
    private final long ownerThread = Thread.currentThread().threadId();
    private final Map<Object, Object> resources;
    private boolean consumed;
    private boolean completed;

    LockedDeviceLifecycleAssetRef(long assetId) {
        if (assetId <= 0 || !TransactionSynchronizationManager.isActualTransactionActive()
                || TransactionSynchronizationManager.getResourceMap().isEmpty()) {
            throw new IllegalStateException("device lifecycle participation requires the asset transaction");
        }
        this.assetId = assetId;
        this.resources = new IdentityHashMap<>(TransactionSynchronizationManager.getResourceMap());
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCompletion(int status) { completed = true; }
        });
    }

    @Override
    public synchronized void withAssetKeyOnce(LongConsumer consumer) {
        if (consumed || completed || Thread.currentThread().threadId() != ownerThread
                || !TransactionSynchronizationManager.isActualTransactionActive()
                || resources.entrySet().stream().anyMatch(entry ->
                    TransactionSynchronizationManager.getResource(entry.getKey()) != entry.getValue())) {
            throw new IllegalStateException("device lifecycle relation cannot leave its transaction or be reused");
        }
        consumed = true;
        consumer.accept(assetId);
    }

    @Override
    public String toString() { return "DeviceLifecycleAssetRef[REDACTED]"; }
}
