package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.OneNetProvisionedDevice;

/** Creates or recovers the one permanent OneNet identity for an enrolled asset. */
public interface OneNetDeviceProvisioningPort {

    OneNetProvisionedDevice ensureDevice(
            String deviceName,
            String descriptionMarker,
            boolean createIfMissing);
}
