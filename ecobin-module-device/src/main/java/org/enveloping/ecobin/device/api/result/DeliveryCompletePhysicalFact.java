package org.enveloping.ecobin.device.api.result;

import java.time.Instant;
import java.util.List;
import java.util.Objects;
import java.util.UUID;

/**
 * The Orange Pi's single final fact for one delivery session.
 */
public record DeliveryCompletePhysicalFact(
        UUID eventUid,
        UUID commandUid,
        UUID sessionUid,
        String hardwareSn,
        String deviceCode,
        long edgeEventSequence,
        Instant deviceOccurredAt,
        String clockQuality,
        String payloadSha256,
        String canonicalSha256,
        int portNo,
        DeliveryCompleteMeasurement firstPreOpenMeasurement,
        DeliveryCompleteMeasurement finalPostCloseMeasurement,
        Long deliveryNetWeightGrams,
        DeliveryCompleteDoorCommand finalDoorCommand,
        String completionReason,
        boolean manualReviewRequired,
        boolean negativeWeightAnomaly,
        long configurationVersion,
        String configurationContentSha256,
        String configurationMcuPayloadSha256,
        long unitPriceTenThousandths,
        List<DeliveryCompletePhoto> photos) {

    public DeliveryCompletePhysicalFact {
        Objects.requireNonNull(eventUid, "eventUid");
        Objects.requireNonNull(commandUid, "commandUid");
        Objects.requireNonNull(sessionUid, "sessionUid");
        Objects.requireNonNull(hardwareSn, "hardwareSn");
        Objects.requireNonNull(deviceCode, "deviceCode");
        Objects.requireNonNull(clockQuality, "clockQuality");
        Objects.requireNonNull(payloadSha256, "payloadSha256");
        Objects.requireNonNull(canonicalSha256, "canonicalSha256");
        Objects.requireNonNull(completionReason, "completionReason");
        Objects.requireNonNull(
                configurationContentSha256,
                "configurationContentSha256");
        Objects.requireNonNull(
                configurationMcuPayloadSha256,
                "configurationMcuPayloadSha256");
        photos = List.copyOf(photos);
    }
}
