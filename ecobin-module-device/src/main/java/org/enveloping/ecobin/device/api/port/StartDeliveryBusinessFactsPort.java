package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.query.StartDeliveryBusinessFactsQuery;
import org.enveloping.ecobin.device.api.result.LockedStartDeliveryBusinessFacts;

/**
 * Device-defined inversion point implemented by recycling. The device calls
 * it only after its asset/deployment/port lock prefix has been acquired.
 */
public interface StartDeliveryBusinessFactsPort {

    LockedStartDeliveryBusinessFacts lockForStart(
            StartDeliveryBusinessFactsQuery query);
}
