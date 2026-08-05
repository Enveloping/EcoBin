package org.enveloping.ecobin.identity.api.persistence;

import java.util.Map;

/**
 * Transaction-bound relationship from an identity registration to a
 * device-owned deployment. The database key is visible only inside the
 * named device overview consumer callback.
 */
public final class RegistrationDeploymentAttributionRef {

    private final long deploymentKey;
    private final long registeredUserCount;
    private final TransactionBoundReferenceGuard guard;

    RegistrationDeploymentAttributionRef(
            long deploymentKey,
            long registeredUserCount,
            Map<Object, Object> resources) {
        this.deploymentKey = deploymentKey;
        this.registeredUserCount = registeredUserCount;
        this.guard = new TransactionBoundReferenceGuard(resources, true);
    }

    public synchronized <T> T resolveOnce(Resolver<T> resolver) {
        guard.claimOnce();
        return resolver.resolve(deploymentKey, registeredUserCount);
    }

    synchronized void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "RegistrationDeploymentAttributionRef[REDACTED]";
    }

    @FunctionalInterface
    public interface Resolver<T> {
        T resolve(long deploymentKey, long registeredUserCount);
    }
}
