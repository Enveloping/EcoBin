package org.enveloping.ecobin.identity.api.persistence;

import java.util.UUID;

/** Operations-owned batch of identity relationships, consumed only by IAM. */
@FunctionalInterface
public interface GovernanceIdentityBatchRef {

    void consumeOnce(EntrySink sink);

    @FunctionalInterface
    interface EntrySink {
        void entry(
                UUID token,
                Long tenantKey,
                Long organizationKey,
                String actorKind,
                Long platformAdminKey,
                Long staffAccountKey);
    }
}
