package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.api.value.DeliveryIdentityFactToken;

/**
 * Caller-owned, transaction-bound batch of identity relationship keys.
 *
 * <p>The caller must implement this reference as non-serializable,
 * thread-bound, transaction-bound and single-consumption. Identity invokes
 * {@link #consumeOnce(EntrySink)} exactly once inside the caller transaction.
 * Internal keys must not be exposed by getters, logs, caches or transport
 * objects.</p>
 */
@FunctionalInterface
public interface DeliveryOrderIdentityBatchRef {

    void consumeOnce(EntrySink sink);

    interface EntrySink {

        void organizationUser(
                DeliveryIdentityFactToken token,
                long tenantKey,
                long organizationKey,
                long organizationUserKey);

        void reviewer(
                DeliveryIdentityFactToken token,
                long tenantKey,
                long organizationKey,
                ReviewerKind reviewerKind,
                Long platformAdminKey,
                Long staffAccountKey);
    }

    enum ReviewerKind {
        PLATFORM_ADMIN,
        STAFF
    }
}
