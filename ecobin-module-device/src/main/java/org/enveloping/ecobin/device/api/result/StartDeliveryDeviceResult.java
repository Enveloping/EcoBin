package org.enveloping.ecobin.device.api.result;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/**
 * Durable acceptance of one delivery session. It is not proof that OneNet or
 * the Orange Pi has executed the command.
 */
public record StartDeliveryDeviceResult(
        UUID sessionUid,
        UUID commandUid,
        Instant authorizationExpiresAt,
        String deploymentCode,
        int portNo) {

    public StartDeliveryDeviceResult {
        Objects.requireNonNull(sessionUid, "sessionUid");
        Objects.requireNonNull(commandUid, "commandUid");
        Objects.requireNonNull(
                authorizationExpiresAt,
                "authorizationExpiresAt");
        if (deploymentCode == null || deploymentCode.isBlank()) {
            throw new IllegalArgumentException(
                    "deploymentCode must not be blank");
        }
        if (portNo < 1 || portNo > 6) {
            throw new IllegalArgumentException(
                    "portNo must be between 1 and 6");
        }
    }
}
