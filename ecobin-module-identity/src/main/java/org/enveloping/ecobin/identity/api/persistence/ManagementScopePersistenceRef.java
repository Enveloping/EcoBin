package org.enveloping.ecobin.identity.api.persistence;

import java.util.List;
import java.util.Map;

/** Transaction-bound, single-use keys for management read models. */
public final class ManagementScopePersistenceRef {

    private final Long tenantKey;
    private final List<Long> organizationKeys;
    private final List<String> organizationCodes;
    private final Long platformAdminKey;
    private final Long staffAccountKey;
    private final TransactionBoundReferenceGuard guard;

    ManagementScopePersistenceRef(
            Long tenantKey,
            List<Long> organizationKeys,
            List<String> organizationCodes,
            Long platformAdminKey,
            Long staffAccountKey,
            Map<Object, Object> resources) {
        this.tenantKey = tenantKey;
        this.organizationKeys = List.copyOf(organizationKeys);
        this.organizationCodes = List.copyOf(organizationCodes);
        if (this.organizationKeys.size() != this.organizationCodes.size()) {
            throw new IllegalArgumentException(
                    "organization keys and codes differ");
        }
        this.platformAdminKey = platformAdminKey;
        this.staffAccountKey = staffAccountKey;
        this.guard = new TransactionBoundReferenceGuard(resources);
    }

    public synchronized <T> T withScopeOnce(ScopeFunction<T> function) {
        guard.claimOnce();
        return function.apply(
                tenantKey,
                organizationKeys,
                platformAdminKey,
                staffAccountKey);
    }

    public synchronized <T> T withScopeOnce(
            Purpose purpose,
            ScopedFunction<T> function) {
        guard.claimOnce(purpose);
        java.util.ArrayList<OrganizationKey> organizations =
                new java.util.ArrayList<>(organizationKeys.size());
        for (int index = 0; index < organizationKeys.size(); index++) {
            organizations.add(new OrganizationKey(
                    organizationKeys.get(index),
                    organizationCodes.get(index)));
        }
        return function.apply(
                tenantKey,
                List.copyOf(organizations),
                platformAdminKey,
                staffAccountKey);
    }

    synchronized void markTransactionCompleted() {
        guard.markTransactionCompleted();
    }

    @Override
    public String toString() {
        return "ManagementScopePersistenceRef[REDACTED]";
    }

    @FunctionalInterface
    public interface ScopeFunction<T> {
        T apply(
                Long tenantKey,
                List<Long> organizationKeys,
                Long platformAdminKey,
                Long staffAccountKey);
    }

    public enum Purpose {
        IDENTITY_OPERATIONAL_OVERVIEW,
        DEVICE_OPERATIONAL_OVERVIEW,
        RECYCLING_OPERATIONAL_OVERVIEW,
        FUNDS_OPERATIONAL_OVERVIEW,
        OPERATIONS_OPERATIONAL_OVERVIEW,
        OPERATIONS_GOVERNANCE_QUERY,
        OPERATIONS_GOVERNANCE_WRITE,
        OPERATIONS_TECHNICAL_WRITE,
        RECYCLING_FULLNESS_QUERY,
        RECYCLING_BAG_TRACE_QUERY
    }

    public record OrganizationKey(long value, String code) { }

    @FunctionalInterface
    public interface ScopedFunction<T> {
        T apply(
                Long tenantKey,
                List<OrganizationKey> organizations,
                Long platformAdminKey,
                Long staffAccountKey);
    }
}
