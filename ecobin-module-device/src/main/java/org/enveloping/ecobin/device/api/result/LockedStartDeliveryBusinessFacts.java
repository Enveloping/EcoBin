package org.enveloping.ecobin.device.api.result;

import org.enveloping.ecobin.device.api.persistence.DeliverySessionBusinessFactsRef;

import java.util.Objects;
import java.util.UUID;

/**
 * Recycling facts locked after the device port and safe to freeze into the
 * new session.
 */
public record LockedStartDeliveryBusinessFacts(
        UUID bagUid,
        String bagCode,
        DeliverySessionBusinessFactsRef persistenceRef) {

    public LockedStartDeliveryBusinessFacts {
        Objects.requireNonNull(bagUid, "bagUid");
        if (bagCode == null || bagCode.isBlank()) {
            throw new IllegalArgumentException(
                    "bagCode must not be blank");
        }
        Objects.requireNonNull(persistenceRef, "persistenceRef");
    }
}
