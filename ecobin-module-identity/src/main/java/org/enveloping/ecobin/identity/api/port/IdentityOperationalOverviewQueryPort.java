package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.result.IdentityOperationalOverview;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;

import java.time.Instant;

public interface IdentityOperationalOverviewQueryPort {
    IdentityOperationalOverview query(
            ManagementScopePersistenceRef scope,
            Instant from,
            Instant toExclusive);
}
