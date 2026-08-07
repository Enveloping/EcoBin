package org.enveloping.ecobin.recycling.web.v1;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public final class CleanRecordModels {

    private CleanRecordModels() {
    }

    public record CleanRecordItem(
            String cleanRecordNo,
            UUID operationUid,
            UUID cleanerUserUid,
            String deviceCode,
            int portNo,
            String removedBagQr,
            String installedBagQr,
            Instant deviceCompletedAt,
            String originalRecalculatedRemovedNetWeightKg,
            String effectiveRemovedNetWeightKg,
            String effectiveWeightSource,
            String weightReliability,
            String resultKind,
            List<String> anomalyCodes,
            String photoCompleteness,
            String recordRemark,
            long version) {

        public CleanRecordItem {
            anomalyCodes = List.copyOf(anomalyCodes);
        }
    }

    public record CleanRecordSource(
            UUID operationUid,
            UUID eventUid,
            UUID commandUid,
            String deviceCode,
            int portNo,
            UUID cleanerUserUid,
            long cleanConfigVersionNo,
            Instant deviceCompletedAt,
            Instant backendReceivedAt,
            Instant completedAt) {
    }

    public record CleanBagFacts(
            String removedBagBindingState,
            String removedBagQr,
            String installedBagQr) {
    }

    public record CleanWeightFacts(
            String preUnlockStatus,
            String preUnlockWeightKg,
            String oldBaselineState,
            String oldBaselineWeightKg,
            String deviceRemovedNetWeightStatus,
            String deviceRemovedNetWeightKg,
            String recalculatedRemovedNetWeightStatus,
            String recalculatedRemovedNetWeightKg,
            String finalTotalWeightStatus,
            String finalTotalWeightKg,
            String candidateNewBaselineWeightKg) {
    }

    public record CleanBaselineSummary(
            boolean established,
            Long versionNo,
            String installedBagQr,
            String baselineWeightKg,
            Instant establishedAt) {
    }

    public record CleanDetectionSummary(
            UUID detectionUid,
            String status,
            String finalResult,
            String failureCode,
            Instant completedAt) {
    }

    public record MiniappCleanAnomaly(
            String code,
            Instant detectedAt) {
    }

    public record WebCleanAnomaly(
            String code,
            Instant detectedAt,
            Map<String, Object> diagnosticDetails) {
    }

    public record CleanPhoto(
            String position,
            String status,
            String url,
            Instant capturedAt,
            String missingReason) {
    }

    public record CleanEffectiveValue(
            String removedNetWeightKg,
            String source,
            boolean includedInKnownWeightStatistics,
            String recordRemark,
            long version) {
    }

    public record ChangeActor(
            String actorKind,
            UUID actorUid,
            String displayName) {
    }

    public record CleanRecordChange(
            UUID changeUid,
            long fromVersion,
            long toVersion,
            String beforeEffectiveRemovedNetWeightKg,
            String beforeEffectiveWeightSource,
            String beforeRecordRemark,
            String afterEffectiveRemovedNetWeightKg,
            String afterEffectiveWeightSource,
            String afterRecordRemark,
            String reason,
            ChangeActor actor,
            Instant changedAt) {
    }

    public record MiniappCleanRecordDetail(
            String cleanRecordNo,
            CleanRecordSource source,
            CleanBagFacts bags,
            CleanWeightFacts weights,
            CleanBaselineSummary newBaseline,
            CleanDetectionSummary postCleanDetection,
            String resultKind,
            List<MiniappCleanAnomaly> anomalies,
            List<CleanPhoto> photos,
            CleanEffectiveValue effective) {

        public MiniappCleanRecordDetail {
            anomalies = List.copyOf(anomalies);
            photos = List.copyOf(photos);
        }
    }

    public record WebCleanRecordDetail(
            String cleanRecordNo,
            CleanRecordSource source,
            CleanBagFacts bags,
            CleanWeightFacts weights,
            CleanBaselineSummary newBaseline,
            CleanDetectionSummary postCleanDetection,
            String resultKind,
            List<WebCleanAnomaly> anomalies,
            List<CleanPhoto> photos,
            CleanEffectiveValue effective,
            CleanRecordChange latestChange) {

        public WebCleanRecordDetail {
            anomalies = List.copyOf(anomalies);
            photos = List.copyOf(photos);
        }
    }

    public record EffectiveWeightEdit(
            @NotBlank @Size(max = 16) String action,
            @Size(max = 16) String valueKg) {
    }

    public record RemarkEdit(
            @NotBlank @Size(max = 16) String action,
            @Size(max = 500) String value) {
    }

    public record EditCleanRecordRequest(
            @NotNull @Min(1) Long expectedVersion,
            @Valid EffectiveWeightEdit effectiveRemovedNetWeight,
            @Valid RemarkEdit recordRemark,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record EditCleanRecordResult(
            String cleanRecordNo,
            long version,
            String effectiveRemovedNetWeightKg,
            String effectiveWeightSource,
            String recordRemark,
            Instant updatedAt,
            UUID changeUid) {
    }
}
