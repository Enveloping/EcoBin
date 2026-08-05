package org.enveloping.ecobin.identity.api.persistence;

import java.util.UUID;

/** Caller-owned, transaction-bound organization-user identity batch. */
@FunctionalInterface
public interface BagTraceIdentityBatchRef {

    void consumeOnce(EntrySink sink);

    @FunctionalInterface
    interface EntrySink {
        void organizationUser(
                UUID token,
                long tenantKey,
                long organizationKey,
                long organizationUserKey);
    }
}
