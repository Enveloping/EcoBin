package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.command.StartDeliveryDeviceCommand;
import org.enveloping.ecobin.device.api.result.StartDeliveryDeviceResult;

/**
 * Device-owned synchronous participant in the recycling-owned start
 * transaction.
 */
public interface StartDeliveryDeviceParticipationPort {

    StartDeliveryDeviceResult start(StartDeliveryDeviceCommand command);
}
