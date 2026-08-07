package org.enveloping.ecobin.device.api.query;

import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;

import java.util.Objects;

public record DeliveryDeviceOptionsQuery(
        String deviceCode,
        DeliveryQueryOrganizationUserRef organizationUserRef) {

    public DeliveryDeviceOptionsQuery {
        if (deviceCode == null || deviceCode.isBlank()) {
            throw new IllegalArgumentException(
                    "deviceCode must not be blank");
        }
        Objects.requireNonNull(
                organizationUserRef,
                "organizationUserRef");
    }
}
