package org.enveloping.ecobin.device.api.result;

import org.enveloping.ecobin.device.api.persistence.RecyclingDevicePortRef;

public record ResolvedRecyclingDevicePort(
        String deviceCode,
        int portNo,
        RecyclingDevicePortRef persistenceRef) { }
