package org.enveloping.ecobin.funds.api.port;

import org.enveloping.ecobin.funds.api.result.FundsOperationalOverview;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;

import java.time.Instant;

public interface FundsOperationalOverviewQueryPort {
    FundsOperationalOverview query(
            ManagementScopePersistenceRef scope,
            Instant from,
            Instant toExclusive);
}
