package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;

/**
 * Device-owned participant that verifies and persists a final delivery fact,
 * then invokes the recycling writer before ending the session.
 */
public interface CompleteDeliveryDeviceParticipationPort {

    TrustedDeviceEventApplyResult complete(
            TrustedDeviceInboxEvent event,
            DeliveryCompletionBusinessWriter businessWriter);
}
