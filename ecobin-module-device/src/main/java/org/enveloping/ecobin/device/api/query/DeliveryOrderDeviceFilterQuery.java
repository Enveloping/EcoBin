package org.enveloping.ecobin.device.api.query;

import org.enveloping.ecobin.identity.api.persistence.DeliveryScopePersistenceRef;

import java.util.Objects;

/**
 * Public Device filter evaluated inside an authorized organization scope.
 * Deployment code and port number are independently optional, but at least
 * one of them is required.
 */
public record DeliveryOrderDeviceFilterQuery(
        String deploymentCode,
        Integer portNo,
        DeliveryScopePersistenceRef scopeRef) {

    public DeliveryOrderDeviceFilterQuery {
        if (deploymentCode == null && portNo == null) {
            throw new IllegalArgumentException(
                    "deploymentCode or portNo is required");
        }
        if (deploymentCode != null) {
            if (deploymentCode.isBlank()
                    || deploymentCode.length() > 64) {
                throw new IllegalArgumentException(
                        "deploymentCode must contain 1 to 64 characters");
            }
            deploymentCode = deploymentCode.trim();
        }
        if (portNo != null && (portNo < 1 || portNo > 6)) {
            throw new IllegalArgumentException(
                    "portNo must be between 1 and 6");
        }
        Objects.requireNonNull(scopeRef, "scopeRef");
    }
}
