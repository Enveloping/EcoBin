package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.BlockedDeviceCommand;

/** Aligns business state when reliable transport can no longer proceed. */
public interface BlockedDeviceCommandBusinessPort {

    void applyBlockedDeviceCommand(BlockedDeviceCommand command);
}
