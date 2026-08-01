package org.enveloping.ecobin.device.api.query;

import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;

import java.util.Objects;
import java.util.UUID;

public record OwnedDeliverySessionQuery(
        UUID sessionUid,
        DeliveryQueryOrganizationUserRef organizationUserRef) {

    public OwnedDeliverySessionQuery {
        Objects.requireNonNull(sessionUid, "sessionUid");
        Objects.requireNonNull(
                organizationUserRef,
                "organizationUserRef");
    }
}
