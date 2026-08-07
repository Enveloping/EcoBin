package org.enveloping.ecobin.device.api.query;

import org.enveloping.ecobin.identity.api.persistence.DeliveryScopePersistenceRef;

import java.util.Objects;

/**
 * Public Device filter evaluated inside an authorized organization scope.
 * Device public code and port number are independently optional, but at least
 * one of them is required.
 */
public record DeliveryOrderDeviceFilterQuery(
        String deviceCode,
        Integer portNo,
        DeliveryScopePersistenceRef scopeRef) {

    public DeliveryOrderDeviceFilterQuery {
        if (deviceCode == null && portNo == null) {
            throw new IllegalArgumentException(
                    "deviceCode or portNo is required");
        }
        if (deviceCode != null) {
            if (deviceCode.isBlank()
                    || deviceCode.length() > 64) {
                throw new IllegalArgumentException(
                        "deviceCode must contain 1 to 64 characters");
            }
            deviceCode = deviceCode.trim();
        }
        if (portNo != null && (portNo < 1 || portNo > 6)) {
            throw new IllegalArgumentException(
                    "portNo must be between 1 and 6");
        }
        Objects.requireNonNull(scopeRef, "scopeRef");
    }
}
