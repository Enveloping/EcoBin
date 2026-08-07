package org.enveloping.ecobin.device.api.result;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

public record RecyclingDeviceRelationFacts(
        Map<UUID, Port> ports,
        Map<UUID, DeliverySession> deliverySessions,
        Map<UUID, FullnessStateFact> fullnessStateFacts) {

    public RecyclingDeviceRelationFacts {
        ports = Map.copyOf(ports);
        deliverySessions = Map.copyOf(deliverySessions);
        fullnessStateFacts = Map.copyOf(fullnessStateFacts);
    }

    public record Port(String deviceCode, int portNo) { }

    public record DeliverySession(Instant createdAt) { }

    public record FullnessStateFact(
            String fullnessMode,
            String sensorKind,
            String sensorValue,
            long totalWeightG,
            Long baselineWeightG,
            long configuredFullWeightG,
            Long fullnessPercentHundredths,
            long sampleCount,
            long measurementElapsedMs,
            String confirmationBasis) { }
}
