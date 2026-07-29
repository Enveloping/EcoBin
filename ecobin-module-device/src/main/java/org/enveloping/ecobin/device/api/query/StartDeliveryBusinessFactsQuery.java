package org.enveloping.ecobin.device.api.query;

import org.enveloping.ecobin.device.api.persistence.DeviceDeliveryPortRef;
import org.enveloping.ecobin.device.api.value.DeliveryRuleSnapshot;

import java.util.Objects;

public record StartDeliveryBusinessFactsQuery(
        DeviceDeliveryPortRef devicePort,
        DeliveryRuleSnapshot expectedDeliveryRule,
        String fullnessMode) {

    public StartDeliveryBusinessFactsQuery {
        Objects.requireNonNull(devicePort, "devicePort");
        Objects.requireNonNull(
                expectedDeliveryRule,
                "expectedDeliveryRule");
        if (fullnessMode == null || fullnessMode.isBlank()) {
            throw new IllegalArgumentException(
                    "fullnessMode must not be blank");
        }
    }
}
