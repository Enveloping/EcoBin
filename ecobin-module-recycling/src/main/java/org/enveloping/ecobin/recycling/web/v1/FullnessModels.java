package org.enveloping.ecobin.recycling.web.v1;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class FullnessModels {

    private FullnessModels() { }

    public record PortCapacityView(
            String deviceCode,
            int portNo,
            String currentBagQr,
            String baselineState,
            String baselineWeightKg,
            String latestStableTotalWeightKg,
            String rawNetWeightKg,
            String displayedFullnessPercent,
            String detectionGate,
            String confirmedFullnessState,
            String stateEvidence,
            UUID currentStateChangeUid,
            Instant lastReportedAt,
            long version,
            Instant asOf) { }

    public record FullnessMeasurementSummary(
            String mode,
            String sensorKind,
            String sensorValue,
            String totalWeightKg,
            String baselineWeightKg,
            String configuredFullWeightKg,
            String fullnessPercent,
            long sampleCount,
            long elapsedMs,
            String confirmationBasis) { }

    public record FullnessStateChangeItem(
            UUID stateChangeUid,
            String reportedState,
            String disposition,
            String bagQr,
            String sourceWorkType,
            UUID sourceWorkUid,
            long edgeEventSequence,
            FullnessMeasurementSummary measurement,
            Instant deviceOccurredAt,
            Instant backendReceivedAt) { }

    public record FullnessStateChangePage(
            List<FullnessStateChangeItem> items,
            String nextCursor,
            Instant asOf) { }
}
