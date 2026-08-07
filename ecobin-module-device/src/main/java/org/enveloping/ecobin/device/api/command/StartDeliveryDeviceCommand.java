package org.enveloping.ecobin.device.api.command;

import org.enveloping.ecobin.device.api.value.DeliveryRuleSnapshot;
import org.enveloping.ecobin.identity.api.persistence.DeliverySessionOrganizationUserRef;

import java.util.Objects;
import java.util.UUID;

/**
 * Trusted input to the device-owned part of the start-delivery transaction.
 */
public record StartDeliveryDeviceCommand(
        UUID operationUid,
        String deviceCode,
        int portNo,
        DeliverySessionOrganizationUserRef organizationUserRef,
        DeliveryRuleSnapshot deliveryRule) {

    public StartDeliveryDeviceCommand {
        Objects.requireNonNull(operationUid, "operationUid");
        if (operationUid.version() != 4 || operationUid.variant() != 2) {
            throw new IllegalArgumentException(
                    "operationUid must be a UUIDv4");
        }
        if (deviceCode == null || deviceCode.isBlank()) {
            throw new IllegalArgumentException(
                    "deviceCode must not be blank");
        }
        if (portNo < 1 || portNo > 6) {
            throw new IllegalArgumentException(
                    "portNo must be between 1 and 6");
        }
        Objects.requireNonNull(
                organizationUserRef,
                "organizationUserRef");
        Objects.requireNonNull(deliveryRule, "deliveryRule");
    }
}
