package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.persistence.RecyclingDevicePortRef;
import java.util.Map;
import java.util.UUID;

/** Reads the current bag's applied report for already authorized page ports. */
public interface DeviceListFullnessQueryPort {
    Map<UUID, UUID> currentReports(Map<UUID, RecyclingDevicePortRef> ports);
}
