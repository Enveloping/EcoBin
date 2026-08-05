package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.DeviceOperationalOverview;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;
import org.enveloping.ecobin.identity.api.result.IdentityOperationalOverview;

import java.util.List;

public interface DeviceOperationalOverviewQueryPort {
    DeviceOperationalOverview query(
            ManagementScopePersistenceRef scope,
            List<IdentityOperationalOverview.Organization> organizations);
}
