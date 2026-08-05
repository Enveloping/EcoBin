package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.DeviceFaultAlertFact;

import java.util.List;
import java.util.UUID;

/** Supplies current device-fault facts without exposing device-owned tables. */
public interface DeviceOperationalAlertSourcePort {

    List<DeviceFaultAlertFact> loadDeviceFaultAlertFacts(
            List<UUID> currentlyOpenAlertFaultUids);
}
