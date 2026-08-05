package org.enveloping.ecobin.recycling.api.port;

import org.enveloping.ecobin.recycling.api.result.RecyclingOperationalOverview;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;

import java.time.Instant;

public interface RecyclingOperationalOverviewQueryPort {
    RecyclingOperationalOverview query(
            ManagementScopePersistenceRef scope,
            Instant from,
            Instant toExclusive);
}
