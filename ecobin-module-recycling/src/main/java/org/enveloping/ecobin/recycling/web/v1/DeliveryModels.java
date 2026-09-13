package org.enveloping.ecobin.recycling.web.v1;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class DeliveryModels {

    private DeliveryModels() {
    }

    public record DeliverySessionAccepted(
            UUID operationId,
            UUID resourceId,
            UUID sessionUid,
            String status,
            String phase,
            Instant startAuthorizationExpiresAt,
            String statusUrl,
            long recommendedPollAfterMs,
            List<String> nextActions) {
    }

    public record DeliverySessionView(
            UUID sessionUid,
            String status,
            String phase,
            String deviceCode,
            int portNo,
            Instant startedAt,
            Instant endedAt,
            String endReason,
            Instant offlineOccupancyReleasedAt,
            String deliveryOrderNo,
            Long recommendedPollAfterMs,
            List<String> nextActions) {
    }

    public record DeliveryOptionsView(
            String deviceCode,
            String displayName,
            String address,
            boolean deviceBusy,
            Instant asOf,
            List<DeliveryPortOption> ports) {
    }

    public record DeliveryPortOption(
            int portNo,
            String displayName,
            String unitPriceYuanPerKg,
            String fullnessPercent,
            boolean deliveryAllowed,
            List<String> blockers) {
    }
}
