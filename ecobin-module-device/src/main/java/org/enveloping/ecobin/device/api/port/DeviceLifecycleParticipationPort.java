package org.enveloping.ecobin.device.api.port;

import java.time.LocalDateTime;
import org.enveloping.ecobin.device.api.persistence.DeviceLifecycleAssetRef;

/** Synchronous participant in the locked asset's disable/retirement transaction. */
public interface DeviceLifecycleParticipationPort {
    void requireIdle(DeviceLifecycleAssetRef asset);

    void cancelDeviceWork(DeviceLifecycleAssetRef asset, String reasonCode, LocalDateTime now);
}
