package org.enveloping.ecobin.recycling.api.result;

import java.math.BigDecimal;
import java.util.Map;

public record RecyclingOperationalOverview(Map<String, Metrics> byOrganization) {
    public RecyclingOperationalOverview { byOrganization = Map.copyOf(byOrganization); }

    public record Metrics(
            long createdOrderCount,
            long recognizedOrderCount,
            BigDecimal recognizedWeightKg,
            long recognizedCashbackCent,
            long currentPendingReviewCount,
            long createdCleanRecordCount,
            long anomalousCleanRecordCount,
            long currentFullPortCount) { }
}
