package org.enveloping.ecobin.device.api.result;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

public record FullnessStateChangePhysicalFact(
        UUID eventUid,
        UUID stateChangeUid,
        String hardwareSn,
        String deploymentCode,
        long edgeEventSequence,
        Instant deviceOccurredAt,
        String clockQuality,
        String payloadSha256,
        String canonicalSha256,
        int portNo,
        UUID bagUid,
        String state,
        String sourceWorkType,
        UUID sourceWorkUid,
        String fullnessMode,
        String fullnessSensorKind,
        String fullnessSensorValue,
        String confirmationBasis,
        FullnessSampleMeasurement totalWeightMeasurement,
        Long baselineWeightGrams,
        long configuredFullWeightGrams,
        Long fullnessPercentHundredths,
        Boolean weightFull,
        long configurationVersion,
        String configurationContentSha256,
        String configurationMcuPayloadSha256) {

    public FullnessStateChangePhysicalFact {
        Objects.requireNonNull(eventUid, "eventUid");
        Objects.requireNonNull(stateChangeUid, "stateChangeUid");
        Objects.requireNonNull(hardwareSn, "hardwareSn");
        Objects.requireNonNull(deploymentCode, "deploymentCode");
        Objects.requireNonNull(deviceOccurredAt, "deviceOccurredAt");
        Objects.requireNonNull(clockQuality, "clockQuality");
        Objects.requireNonNull(payloadSha256, "payloadSha256");
        Objects.requireNonNull(canonicalSha256, "canonicalSha256");
        Objects.requireNonNull(bagUid, "bagUid");
        Objects.requireNonNull(state, "state");
        Objects.requireNonNull(sourceWorkType, "sourceWorkType");
        Objects.requireNonNull(sourceWorkUid, "sourceWorkUid");
        Objects.requireNonNull(fullnessMode, "fullnessMode");
        Objects.requireNonNull(fullnessSensorKind, "fullnessSensorKind");
        Objects.requireNonNull(fullnessSensorValue, "fullnessSensorValue");
        Objects.requireNonNull(confirmationBasis, "confirmationBasis");
        Objects.requireNonNull(totalWeightMeasurement,
                "totalWeightMeasurement");
    }
}
