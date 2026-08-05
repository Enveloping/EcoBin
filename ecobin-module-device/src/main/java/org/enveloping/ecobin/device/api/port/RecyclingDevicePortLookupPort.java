package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.ResolvedRecyclingDevicePort;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;

public interface RecyclingDevicePortLookupPort {
    ResolvedRecyclingDevicePort requirePort(
            ManagementScopePersistenceRef scope,
            String deploymentCode,
            int portNo);
}
