package org.enveloping.ecobin.device.api.query;

import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;

import java.util.Objects;

public record DeliveryDeviceOptionsQuery(
        String deploymentCode,
        DeliveryQueryOrganizationUserRef organizationUserRef) {

    public DeliveryDeviceOptionsQuery {
        if (deploymentCode == null || deploymentCode.isBlank()) {
            throw new IllegalArgumentException(
                    "deploymentCode must not be blank");
        }
        Objects.requireNonNull(
                organizationUserRef,
                "organizationUserRef");
    }
}
