package org.enveloping.ecobin.recycling.web.v1;

import java.time.Instant;
import java.util.UUID;

/** Web 后台展示的清运操作事实。 */
public final class CleanOperationModels {

    private CleanOperationModels() {
    }

    public record WebCleanOperationItem(
            UUID operationUid,
            String status,
            long version,
            UUID cleanerUserUid,
            String deviceCode,
            int portNo,
            String oldBagBindingState,
            String removedBagQr,
            String installedBagQr,
            boolean edgeSavedConfirmed,
            boolean firstUnlockMayHaveExecuted,
            boolean cleanLockDeenergizedConfirmed,
            boolean cleanerPhysicalCloseConfirmed,
            Instant startAuthorizationExpiresAt,
            Instant executionDeadlineAt,
            Instant createdAt,
            Instant updatedAt,
            Instant endedAt,
            String endReason,
            String cleanRecordNo) {
    }

    public record WebCleanOperationDetail(
            UUID operationUid,
            String status,
            long version,
            UUID cleanerUserUid,
            String deviceCode,
            int portNo,
            String oldBagBindingState,
            String removedBagQr,
            String installedBagQr,
            String preUnlockWeightStatus,
            String preUnlockWeightKg,
            String preUnlockWeightFaultCode,
            boolean edgeSavedConfirmed,
            boolean firstUnlockMayHaveExecuted,
            boolean cleanLockDeenergizedConfirmed,
            boolean cleanerPhysicalCloseConfirmed,
            Instant startAuthorizationExpiresAt,
            Instant edgeSavedAt,
            Instant firstPossibleUnlockAt,
            Instant solenoidPoweredOffAt,
            Instant cleanerConfirmedClosedAt,
            Instant executionDeadlineAt,
            int reopenCount,
            int recoveryCount,
            Instant createdAt,
            Instant updatedAt,
            Instant endedAt,
            String endReason,
            String cleanRecordNo) {
    }
}
