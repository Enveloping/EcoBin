package org.enveloping.ecobin.device.api.result;

import java.util.Objects;

/**
 * Ephemeral OneNet provisioning result. The secret must only be wrapped for
 * the enrolling device and must never be persisted or logged in plaintext.
 */
public record OneNetProvisionedDevice(
        String deviceId,
        String deviceName,
        String description,
        String secretKey) {

    public OneNetProvisionedDevice {
        Objects.requireNonNull(deviceId, "deviceId");
        Objects.requireNonNull(deviceName, "deviceName");
        Objects.requireNonNull(description, "description");
        Objects.requireNonNull(secretKey, "secretKey");
    }
}
