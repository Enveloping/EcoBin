package org.enveloping.ecobin.recycling.api.port;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;

/**
 * Applies one trusted Orange Pi delivery-complete event as a recycling
 * business order in the inbox worker's transaction.
 */
public interface ApplyDeliveryCompleteUseCase {

    TrustedDeviceEventApplyResult apply(
            TrustedDeviceInboxEvent event);
}
