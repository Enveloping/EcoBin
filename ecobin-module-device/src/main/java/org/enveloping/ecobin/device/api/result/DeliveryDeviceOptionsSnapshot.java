package org.enveloping.ecobin.device.api.result;

import org.enveloping.ecobin.device.api.persistence.DeliveryOptionsBusinessQueryRef;

import java.time.Instant;
import java.util.List;
import java.util.Objects;

/**
 * 当前机构下某部署的 device 只读投递快照。
 */
public record DeliveryDeviceOptionsSnapshot(
        String deviceCode,
        String displayName,
        String address,
        boolean deviceBusy,
        Instant asOf,
        List<DeliveryDevicePortOptionSnapshot> ports,
        DeliveryOptionsBusinessQueryRef businessQueryRef) {

    public DeliveryDeviceOptionsSnapshot {
        if (deviceCode == null || deviceCode.isBlank()) {
            throw new IllegalArgumentException(
                    "deviceCode must not be blank");
        }
        Objects.requireNonNull(asOf, "asOf");
        ports = List.copyOf(ports);
        Objects.requireNonNull(businessQueryRef, "businessQueryRef");
    }
}
