package org.enveloping.ecobin.device.api.result;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

public record FullnessSamplePhysicalFact(
        UUID eventUid,
        UUID commandUid,
        UUID detectionUid,
        String hardwareSn,
        String deviceCode,
        long edgeEventSequence,
        Instant deviceOccurredAt,
        String clockQuality,
        String payloadSha256,
        String canonicalSha256,
        int portNo,
        String sampleRole,
        String triggerType,
        String fullnessMode,
        String fullnessSensorKind,
        String fullnessSensorValue,
        String fullnessSampleBasis,
        Long representativeDistanceMm,
        int requestedSampleCount,
        int validSampleCount,
        FullnessSampleMeasurement totalWeightMeasurement,
        long configurationVersion,
        String configurationContentSha256,
        String configurationMcuPayloadSha256) {

    public FullnessSamplePhysicalFact {
        Objects.requireNonNull(eventUid, "eventUid");
        Objects.requireNonNull(commandUid, "commandUid");
        Objects.requireNonNull(detectionUid, "detectionUid");
        Objects.requireNonNull(hardwareSn, "hardwareSn");
        Objects.requireNonNull(deviceCode, "deviceCode");
        Objects.requireNonNull(deviceOccurredAt, "deviceOccurredAt");
        Objects.requireNonNull(clockQuality, "clockQuality");
        Objects.requireNonNull(payloadSha256, "payloadSha256");
        Objects.requireNonNull(canonicalSha256, "canonicalSha256");
        Objects.requireNonNull(sampleRole, "sampleRole");
        Objects.requireNonNull(triggerType, "triggerType");
        Objects.requireNonNull(fullnessMode, "fullnessMode");
        Objects.requireNonNull(fullnessSensorKind, "fullnessSensorKind");
        Objects.requireNonNull(fullnessSensorValue, "fullnessSensorValue");
        Objects.requireNonNull(fullnessSampleBasis, "fullnessSampleBasis");
        Objects.requireNonNull(
                totalWeightMeasurement,
                "totalWeightMeasurement");
    }
}
