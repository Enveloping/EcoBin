package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.persistence.GovernanceIdentityBatchRef;
import org.enveloping.ecobin.identity.api.persistence.GovernanceIdentityFilterRef;
import org.enveloping.ecobin.identity.api.result.GovernanceIdentityFacts;

import java.util.UUID;

public interface GovernanceIdentityQueryPort {

    GovernanceIdentityFilterRef prepareFilter(
            String organizationCode, UUID actorUid);

    GovernanceIdentityFacts resolve(GovernanceIdentityBatchRef batch);
}
