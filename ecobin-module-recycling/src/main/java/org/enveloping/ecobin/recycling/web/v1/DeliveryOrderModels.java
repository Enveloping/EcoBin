package org.enveloping.ecobin.recycling.web.v1;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public final class DeliveryOrderModels {

    private DeliveryOrderModels() {
    }

    public record CursorPage<T>(
            List<T> items,
            Instant asOf,
            String nextCursor) {

        public CursorPage {
            items = List.copyOf(items);
        }
    }

    public record MiniappDeliveryOrderItem(
            String deliveryOrderNo,
            String deploymentCode,
            int portNo,
            Instant deviceOccurredAt,
            Instant receivedAt,
            String rawWeightKg,
            String rawAmountYuan,
            String rawWeightReliability,
            String rawAmountReliability,
            String reviewStatus,
            long currentRevisionNo,
            String finalWeightKg,
            String finalAmountYuan,
            List<String> anomalyCodes,
            String photoCompleteness) {

        public MiniappDeliveryOrderItem {
            anomalyCodes = List.copyOf(anomalyCodes);
        }
    }

    public record WebDeliveryOrderItem(
            String deliveryOrderNo,
            UUID organizationUserUid,
            String deploymentCode,
            int portNo,
            Instant deviceOccurredAt,
            Instant receivedAt,
            String rawWeightKg,
            String rawAmountYuan,
            String rawWeightReliability,
            String rawAmountReliability,
            String reviewStatus,
            long currentRevisionNo,
            String finalWeightKg,
            String finalAmountYuan,
            List<String> anomalyCodes,
            String photoCompleteness) {

        public WebDeliveryOrderItem {
            anomalyCodes = List.copyOf(anomalyCodes);
        }
    }

    public record DeliverySource(
            UUID eventUid,
            UUID sessionUid,
            String deploymentCode,
            int portNo,
            Instant deviceOccurredAt,
            Instant receivedAt) {
    }

    public record DeliveryOwnership(UUID organizationUserUid) {
    }

    public record DeliveryRawFacts(
            Long firstPreOpenWeightGram,
            Long finalPostCloseWeightGram,
            Long netWeightGram,
            String weightKg,
            String unitPriceYuanPerKg,
            String amountYuan,
            String weightReliability,
            String amountReliability,
            boolean negativeWeightAnomaly) {
    }

    public record DeliveryReviewProjection(
            String status,
            long currentRevisionNo,
            String maxReviewAbsoluteWeightKg,
            String finalWeightKg,
            String finalAmountYuan,
            Instant firstApprovedAt) {
    }

    public record MiniappDeliveryAnomaly(
            String category,
            String code,
            Instant detectedAt,
            String message) {
    }

    public record WebDeliveryAnomaly(
            String category,
            String code,
            Instant detectedAt,
            String message,
            Map<String, Object> diagnosticDetails) {
    }

    public record DeliveryPhoto(
            String position,
            String status,
            String url,
            Instant capturedAt,
            String missingReason) {
    }

    public record DeliveryRevisionOperator(
            String actorKind,
            UUID actorUid,
            String displayName) {
    }

    public record DeliveryRevision(
            UUID revisionUid,
            long revisionNo,
            String revisionType,
            String decision,
            String beforeFinalWeightKg,
            String beforeFinalAmountYuan,
            String afterFinalWeightKg,
            String afterFinalAmountYuan,
            String amountDeltaYuan,
            String reason,
            DeliveryRevisionOperator operator,
            Instant reviewedAt) {
    }

    public record MiniappDeliveryOrderDetail(
            String deliveryOrderNo,
            DeliverySource source,
            DeliveryRawFacts raw,
            DeliveryReviewProjection review,
            List<MiniappDeliveryAnomaly> anomalies,
            List<DeliveryPhoto> photos) {

        public MiniappDeliveryOrderDetail {
            anomalies = List.copyOf(anomalies);
            photos = List.copyOf(photos);
        }
    }

    public record WebDeliveryOrderDetail(
            String deliveryOrderNo,
            DeliverySource source,
            DeliveryOwnership ownership,
            DeliveryRawFacts raw,
            DeliveryReviewProjection review,
            List<WebDeliveryAnomaly> anomalies,
            List<DeliveryPhoto> photos,
            List<DeliveryRevision> revisions) {

        public WebDeliveryOrderDetail {
            anomalies = List.copyOf(anomalies);
            photos = List.copyOf(photos);
            revisions = List.copyOf(revisions);
        }
    }

    public record ReviewDeliveryOrderRequest(
            @NotNull @Min(0) Long expectedRevisionNo,
            @NotBlank @Size(max = 24) String decision,
            @Size(max = 64) String finalWeightKg,
            @Size(max = 500) String reason) {
    }

    public record PreviewDeliveryReviewRequest(
            @NotNull @Min(0) Long expectedRevisionNo,
            @NotBlank @Size(max = 24) String decision,
            @Size(max = 64) String finalWeightKg) {
    }

    public record DeliveryReviewPreview(
            String deliveryOrderNo,
            String revisionType,
            long expectedRevisionNo,
            String decision,
            String finalWeightKg,
            String finalAmountYuan,
            String walletDeltaYuan,
            String walletEffect,
            Instant previewedAt) {
    }

    public record DeliveryReviewResult(
            String deliveryOrderNo,
            UUID revisionUid,
            long revisionNo,
            String reviewStatus,
            String decision,
            String finalWeightKg,
            String finalAmountYuan,
            String walletDeltaYuan,
            String walletEffect,
            Instant reviewedAt) {
    }
}
