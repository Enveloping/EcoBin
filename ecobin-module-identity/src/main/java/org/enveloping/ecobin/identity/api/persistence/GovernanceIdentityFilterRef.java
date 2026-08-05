package org.enveloping.ecobin.identity.api.persistence;

import java.util.List;
import java.util.Map;

/** Identity-owned, single-use filter keys for an operations query. */
public final class GovernanceIdentityFilterRef {

    private final boolean organizationRequested;
    private final List<Long> organizationKeys;
    private final boolean actorRequested;
    private final List<Long> platformAdminKeys;
    private final List<Long> staffAccountKeys;
    private final TransactionBoundReferenceGuard guard;

    GovernanceIdentityFilterRef(
            boolean organizationRequested,
            List<Long> organizationKeys,
            boolean actorRequested,
            List<Long> platformAdminKeys,
            List<Long> staffAccountKeys,
            Map<Object, Object> resources) {
        this.organizationRequested = organizationRequested;
        this.organizationKeys = List.copyOf(organizationKeys);
        this.actorRequested = actorRequested;
        this.platformAdminKeys = List.copyOf(platformAdminKeys);
        this.staffAccountKeys = List.copyOf(staffAccountKeys);
        this.guard = new TransactionBoundReferenceGuard(resources);
    }

    public synchronized <T> T consumeOnce(FilterFunction<T> function) {
        guard.claimOnce();
        return function.apply(
                organizationRequested, organizationKeys,
                actorRequested, platformAdminKeys, staffAccountKeys);
    }

    synchronized void markCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "GovernanceIdentityFilterRef[REDACTED]";
    }

    @FunctionalInterface
    public interface FilterFunction<T> {
        T apply(
                boolean organizationRequested,
                List<Long> organizationKeys,
                boolean actorRequested,
                List<Long> platformAdminKeys,
                List<Long> staffAccountKeys);
    }
}
