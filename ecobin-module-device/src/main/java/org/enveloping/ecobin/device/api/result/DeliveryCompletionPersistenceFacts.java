package org.enveloping.ecobin.device.api.result;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.UUID;

/**
 * Facts exposed only inside a single-use completion callback. Public business
 * identities remain separate from the database keys.
 */
public record DeliveryCompletionPersistenceFacts(
        long tenantId,
        long organizationId,
        long assetId,
        long deploymentId,
        long portId,
        long deliverySessionId,
        long organizationUserId,
        long commandId,
        long edgeEventId,
        long physicalResultId,
        long deviceConfigVersionId,
        long portConfigSnapshotId,
        long deliveryConfigVersionId,
        byte[] deliveryConfigContentSha256,
        long bagId,
        UUID bagUid,
        String bagCode,
        BigDecimal unitPriceYuanPerKg,
        long openBalanceFloorCent,
        long maxReviewAbsWeightGrams,
        long negativeWeightThresholdGrams,
        String fullnessMode,
        long configuredFullWeightGrams,
        long fullnessSettleWaitMs,
        long fullnessConfirmationWaitMs,
        long fullnessMeasurementTimeoutMs,
        LocalDateTime backendReceivedAt,
        DeliveryCompletePhysicalFact physicalFact) {

    public DeliveryCompletionPersistenceFacts {
        deliveryConfigContentSha256 =
                deliveryConfigContentSha256.clone();
    }

    @Override
    public byte[] deliveryConfigContentSha256() {
        return deliveryConfigContentSha256.clone();
    }
}
