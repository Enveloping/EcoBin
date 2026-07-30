package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFactsRef;
import org.enveloping.ecobin.device.api.result.DeliveryOrderDeviceFacts;

import java.util.List;

/**
 * Resolves Device-owned public facts for Recycling delivery-order rows.
 */
public interface DeliveryOrderDeviceFactsQueryPort {

    List<DeliveryOrderDeviceFacts> facts(
            DeliveryOrderDeviceFactsRef factsRef);
}
