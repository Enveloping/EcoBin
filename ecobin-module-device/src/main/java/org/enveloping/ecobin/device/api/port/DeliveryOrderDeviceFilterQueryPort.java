package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFilterRef;
import org.enveloping.ecobin.device.api.query.DeliveryOrderDeviceFilterQuery;

import java.util.Optional;

/**
 * Resolves public Device filter values to an opaque Recycling query scope.
 */
public interface DeliveryOrderDeviceFilterQueryPort {

    Optional<DeliveryOrderDeviceFilterRef> resolveFilter(
            DeliveryOrderDeviceFilterQuery query);
}
