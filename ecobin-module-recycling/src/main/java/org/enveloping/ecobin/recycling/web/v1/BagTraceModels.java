package org.enveloping.ecobin.recycling.web.v1;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class BagTraceModels {

    private BagTraceModels() { }

    public record BagUseCycleItem(
            UUID cycleUid,
            String status,
            String startBasis,
            String deploymentCode,
            int portNo,
            Instant installedAt,
            Instant removedAt,
            long deliveryOrderCount) { }

    public record BagUseCyclePage(
            String bagQr,
            String codeAuthKind,
            long unassignedLegacyDeliveryCount,
            List<BagUseCycleItem> items,
            Instant asOf,
            String nextCursor) { }

    public record BagTraceUser(
            UUID organizationUserUid,
            String nickname,
            String maskedPhoneNumber) { }

    public record BagTraceDeliveryOrderItem(
            String deliveryOrderNo,
            BagTraceUser user,
            Instant deviceOccurredAt,
            Instant receivedAt,
            String rawWeightKg,
            String rawAmountYuan,
            String finalWeightKg,
            String finalAmountYuan,
            String reviewStatus,
            String reason,
            String photoCompleteness) { }

    public record BagTraceDeliveryOrderPage(
            String bagQr,
            UUID cycleUid,
            List<BagTraceDeliveryOrderItem> items,
            Instant asOf,
            String nextCursor) { }

    public record BagTracePhoto(
            String position,
            String status,
            String url,
            Instant capturedAt,
            String missingReason) { }

    public record BagTraceDeliveryOrderDetail(
            String bagQr,
            UUID cycleUid,
            BagTraceDeliveryOrderItem order,
            List<BagTracePhoto> photos) { }

    public record BagCurrentOccupancy(
            String kind,
            String deploymentCode,
            Integer portNo,
            UUID cleanOperationUid,
            Instant since) { }

    public record WebBagDetail(
            String bagQr,
            Instant registeredAt,
            BagCurrentOccupancy currentOccupancy,
            Instant lastRelationChangedAt) { }

    public record BagOccupancyEventItem(
            UUID eventUid,
            String eventType,
            String deploymentCode,
            int portNo,
            UUID cleanOperationUid,
            Instant occurredAt,
            String sourceType,
            String sourceReference) { }

    public record BagOccupancyEventPage(
            String bagQr,
            List<BagOccupancyEventItem> items,
            Instant asOf,
            String nextCursor) { }

    public record BagCleanRecordItem(
            String cleanRecordNo,
            UUID operationUid,
            UUID cleanerUserUid,
            String deploymentCode,
            int portNo,
            String relationRole,
            String removedBagQr,
            String installedBagQr,
            Instant deviceCompletedAt,
            String originalRecalculatedRemovedNetWeightKg,
            String effectiveRemovedNetWeightKg,
            String effectiveWeightSource,
            String weightReliability,
            String resultKind,
            String photoCompleteness,
            String recordRemark,
            long version) { }

    public record BagCleanRecordPage(
            String bagQr,
            List<BagCleanRecordItem> items,
            Instant asOf,
            String nextCursor) { }

    public record WebBagDeliveryOrderPage(
            String bagQr,
            List<BagTraceDeliveryOrderItem> items,
            Instant asOf,
            String nextCursor) { }
}
