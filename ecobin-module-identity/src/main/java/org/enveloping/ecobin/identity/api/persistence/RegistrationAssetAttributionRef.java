package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;

/**
 * Transaction-bound relationship from an identity registration to a
 * permanent device asset. The database key is visible only inside the
 * named device overview consumer callback.
 */
public final class RegistrationAssetAttributionRef {

    private final long assetKey;
    private final long registeredUserCount;
    private final TransactionBoundReferenceGuard guard;

    RegistrationAssetAttributionRef(
            long assetKey,
            long registeredUserCount,
            Map<Object, Object> resources) {
        this.assetKey = assetKey;
        this.registeredUserCount = registeredUserCount;
        this.guard = new TransactionBoundReferenceGuard(resources, true);
    }

    public synchronized <T> T resolveOnce(Resolver<T> resolver) {
        guard.claimOnce();
        return resolver.resolve(assetKey, registeredUserCount);
    }

    synchronized void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "RegistrationAssetAttributionRef[REDACTED]";
    }

    @FunctionalInterface
    public interface Resolver<T> {
        T resolve(long assetKey, long registeredUserCount);
    }
}
