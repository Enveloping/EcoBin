package org.enveloping.ecobin.recycling.application.deliveryquery;

import org.enveloping.ecobin.device.api.persistence.DeliveryOptionsBusinessQueryRef;
import org.enveloping.ecobin.device.api.persistence.DeliverySessionBusinessQueryRef;

import java.util.Optional;

/**
 * Recycling-owned read side used by the miniapp delivery query coordinator.
 *
 * <p>The device module supplies transaction-bound references, so callers never
 * pass recycling raw tenant, organization, deployment, port or session keys.</p>
 */
public interface DeliveryBusinessReadPort {

    DeliveryOptionsBusinessFacts currentOptions(
            DeliveryOptionsBusinessQueryRef queryRef);

    Optional<String> findDeliveryOrderNo(
            DeliverySessionBusinessQueryRef queryRef);
}
