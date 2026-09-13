package org.enveloping.ecobin.recycling.application.device;

import org.enveloping.ecobin.device.api.port.TrustedCleanCommandObservationBusinessPort;
import org.enveloping.ecobin.device.api.result.TrustedCleanCommandObservation;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

/** Projects trusted edge command evidence into the cleaning state machine. */
@Service
public class ApplyCleanCommandObservationService
        implements TrustedCleanCommandObservationBusinessPort {

    private final JdbcTemplate jdbc;

    public ApplyCleanCommandObservationService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void applyCleanCommandObservation(
            TrustedCleanCommandObservation observation) {
        if (!"START_CLEAN_OPERATION".equals(
                observation.commandType())) {
            return;
        }
        List<OperationRow> rows = jdbc.query("""
                        SELECT id, port_id, new_bag_id, status,
                               end_reason,
                               first_unlock_may_have_executed,
                               created_at,
                               offline_occupancy_released_at
                        FROM rec_clean_operation
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new OperationRow(
                        rs.getLong("id"),
                        rs.getLong("port_id"),
                        rs.getLong("new_bag_id"),
                        rs.getString("status"),
                        rs.getString("end_reason"),
                        rs.getBoolean(
                                "first_unlock_may_have_executed"),
                        rs.getObject(
                                "created_at", LocalDateTime.class),
                        rs.getObject(
                                "offline_occupancy_released_at",
                                LocalDateTime.class)),
                observation.cleanOperationId(),
                observation.tenantId(),
                observation.organizationId(),
                observation.assetId());
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "trusted clean command target is missing");
        }
        OperationRow operation = rows.getFirst();
        var action = CleanCommandObservationDecision.decide(
                observation.commandType(),
                operation.status(),
                operation.firstUnlockMayHaveExecuted(),
                observation.stage(),
                observation.errorCode());
        LocalDateTime occurredAt = trustedOperationTime(
                observation.occurredAt(), operation.createdAt(), observation.receivedAt());
        switch (action) {
            case MARK_EDGE_SAVED -> markEdgeSaved(
                    observation, operation, occurredAt);
            case MARK_IN_PROGRESS -> markInProgress(
                    observation, operation, occurredAt);
            case END_BEFORE_UNLOCK -> endBeforeUnlock(
                    observation, operation);
            case REQUIRE_RECOVERY -> requireRecovery(
                    observation, operation);
            case NONE -> {
                // Duplicate, stale and non-start observations remain evidence.
            }
        }
    }

    private void markEdgeSaved(
            TrustedCleanCommandObservation observation,
            OperationRow operation,
            LocalDateTime occurredAt) {
        requireSingle(jdbc.update("""
                        UPDATE rec_clean_operation
                        SET status = CASE
                                WHEN status = 'PREPARED'
                                THEN 'EDGE_SAVED'
                                ELSE status
                            END,
                            edge_saved_confirmed = 1,
                            edge_saved_at = COALESCE(edge_saved_at, ?),
                            execution_deadline_at = COALESCE(
                                execution_deadline_at,
                                CASE WHEN ? IS NULL THEN NULL ELSE
                                    TIMESTAMPADD(
                                        SECOND,
                                        operation_timeout_seconds,
                                        ?)
                                END
                            ),
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status IN (
                              'PREPARED', 'EDGE_SAVED',
                              'IN_PROGRESS', 'RECOVERY_REQUIRED'
                          )
                        """,
                occurredAt,
                occurredAt,
                occurredAt,
                observation.receivedAt(),
                operation.id(),
                observation.tenantId(),
                observation.organizationId(),
                observation.assetId()),
                "project clean edge acceptance");
    }

    private void markInProgress(
            TrustedCleanCommandObservation observation,
            OperationRow operation,
            LocalDateTime occurredAt) {
        requireSingle(jdbc.update("""
                        UPDATE rec_clean_operation
                        SET status = CASE
                                WHEN status IN ('PREPARED', 'EDGE_SAVED')
                                THEN 'IN_PROGRESS'
                                ELSE status
                            END,
                            edge_saved_confirmed = 1,
                            first_unlock_may_have_executed = 1,
                            first_possible_unlock_at = COALESCE(
                                first_possible_unlock_at, ?),
                            execution_deadline_at = COALESCE(
                                execution_deadline_at,
                                CASE WHEN edge_saved_at IS NULL THEN NULL ELSE
                                    TIMESTAMPADD(
                                        SECOND,
                                        operation_timeout_seconds,
                                        edge_saved_at)
                                END
                            ),
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status IN (
                              'PREPARED', 'EDGE_SAVED',
                              'IN_PROGRESS', 'RECOVERY_REQUIRED'
                          )
                        """,
                occurredAt,
                observation.receivedAt(),
                operation.id(),
                observation.tenantId(),
                observation.organizationId(),
                observation.assetId()),
                "project clean physical start boundary");
    }

    private void requireRecovery(
            TrustedCleanCommandObservation observation,
            OperationRow operation) {
        String reason = observation.errorCode();
        if (!("MCU_RESTART_FINAL_RESULT_UNAVAILABLE".equals(reason)
                || "MCU_COMMUNICATION_UNAVAILABLE".equals(reason))) {
            throw new IllegalArgumentException(
                    "interrupted clean requires a supported terminal fault");
        }
        requireSingle(jdbc.update("""
                        UPDATE rec_clean_operation
                        SET status = 'ABORTED',
                            edge_saved_confirmed = 1,
                            first_unlock_may_have_executed = 1,
                            ended_at = ?,
                            end_reason = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status IN (
                              'PREPARED', 'EDGE_SAVED',
                              'IN_PROGRESS', 'RECOVERY_REQUIRED'
                          )
                        """,
                observation.receivedAt(),
                reason,
                observation.receivedAt(),
                operation.id(),
                observation.tenantId(),
                observation.organizationId(),
                observation.assetId()),
                "terminate interrupted clean before manual bag recovery");
        ensureBagRecoveryInterlock(observation, operation);
    }

    private void ensureBagRecoveryInterlock(
            TrustedCleanCommandObservation observation,
            OperationRow operation) {
        List<Long> existing = jdbc.query("""
                        SELECT source_clean_operation_id
                        FROM rec_port_clean_restart_interlock
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong(
                        "source_clean_operation_id"),
                observation.tenantId(),
                observation.organizationId(),
                observation.assetId(),
                operation.portId());
        if (existing.size() == 1
                && existing.getFirst() == operation.id()) {
            return;
        }
        if (!existing.isEmpty()) {
            throw new IllegalStateException(
                    "another interrupted clean owns the port interlock");
        }
        requireSingle(jdbc.update("""
                        INSERT INTO rec_port_clean_restart_interlock (
                            tenant_id, organization_id, asset_id,
                            port_id, source_clean_operation_id,
                            activated_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                observation.tenantId(),
                observation.organizationId(),
                observation.assetId(),
                operation.portId(),
                operation.id(),
                observation.receivedAt(),
                observation.receivedAt(),
                observation.receivedAt()),
                "create interrupted clean bag interlock");
    }

    private void endBeforeUnlock(
            TrustedCleanCommandObservation observation,
            OperationRow operation) {
        String reason = observation.errorCode();
        if (reason == null || reason.isBlank() || reason.length() > 64) {
            throw new IllegalArgumentException(
                    "pre-unlock clean failure requires a stable error code");
        }
        requireMatchingOrReleasedOccupancy(observation, operation);
        requireSingle(jdbc.update("""
                        UPDATE rec_clean_operation
                        SET status = 'PRE_UNLOCK_ENDED',
                            ended_at = ?,
                            end_reason = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status IN ('PREPARED', 'EDGE_SAVED')
                          AND first_unlock_may_have_executed = 0
                        """,
                observation.receivedAt(),
                reason,
                observation.receivedAt(),
                operation.id(),
                observation.tenantId(),
                observation.organizationId(),
                observation.assetId()),
                "end clean before unlock");
        requireOccupancyRelease(jdbc.update("""
                        DELETE FROM dev_device_occupancy
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND occupancy_kind = 'CLEAN'
                          AND clean_operation_id = ?
                        """,
                observation.tenantId(),
                observation.organizationId(),
                observation.assetId(),
                operation.id()),
                operation.offlineOccupancyReleasedAt(),
                "release pre-unlock clean occupancy");
        requireSingle(jdbc.update("""
                        DELETE FROM rec_bag_current_occupancy
                        WHERE bag_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND occupancy_type = 'CLEAN_RESERVED'
                          AND clean_operation_id = ?
                        """,
                operation.newBagId(),
                observation.tenantId(),
                observation.organizationId(),
                operation.id()),
                "release pre-unlock clean bag reservation");
        requireSingle(jdbc.update("""
                        INSERT INTO rec_bag_occupancy_event (
                            event_uid, tenant_id, organization_id,
                            bag_id, port_id, clean_operation_id,
                            event_type, occurred_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?,
                            'RESERVATION_RELEASED', ?, ?
                        )
                        """,
                UUID.randomUUID().toString(),
                observation.tenantId(),
                observation.organizationId(),
                operation.newBagId(),
                operation.portId(),
                operation.id(),
                observation.receivedAt(),
                observation.receivedAt()),
                "record pre-unlock clean bag release");
        jdbc.update("""
                        UPDATE rec_clean_photo
                        SET status = 'PERMANENTLY_MISSING',
                            linked_at = ?,
                            missing_reason = ?,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND clean_operation_id = ?
                          AND status = 'UPLOAD_PENDING'
                        """,
                observation.receivedAt(),
                reason,
                observation.receivedAt(),
                observation.tenantId(),
                observation.organizationId(),
                operation.id());
    }

    private void requireMatchingOrReleasedOccupancy(
            TrustedCleanCommandObservation observation,
            OperationRow operation) {
        List<OccupancyRow> rows = jdbc.query("""
                        SELECT occupancy_kind,
                               delivery_session_id,
                               clean_operation_id
                        FROM dev_device_occupancy
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new OccupancyRow(
                        rs.getString("occupancy_kind"),
                        nullableLong(rs, "delivery_session_id"),
                        nullableLong(rs, "clean_operation_id")),
                observation.tenantId(),
                observation.organizationId(),
                observation.assetId());
        boolean valid = operation.offlineOccupancyReleasedAt() == null
                ? rows.size() == 1
                    && rows.getFirst().matchesClean(operation.id())
                : rows.isEmpty();
        if (!valid) {
            throw new IllegalStateException(
                    "trusted clean command occupancy differs");
        }
    }

    static LocalDateTime trustedOperationTime(
            LocalDateTime occurredAt,
            LocalDateTime createdAt,
            LocalDateTime receivedAt) {
        // The device timestamp is retained as evidence only.  State changes
        // are ordered by the backend receive clock.
        return receivedAt;
    }

    private static void requireSingle(int updated, String action) {
        if (updated != 1) {
            throw new IllegalStateException(
                    action + " expected one row but updated " + updated);
        }
    }

    private static void requireOccupancyRelease(
            int updated,
            LocalDateTime offlineReleasedAt,
            String action) {
        int expected = offlineReleasedAt == null ? 1 : 0;
        if (updated != expected) {
            throw new IllegalStateException(
                    action + " expected " + expected
                            + " rows but updated " + updated);
        }
    }

    private static Long nullableLong(
            java.sql.ResultSet rs,
            String column) throws java.sql.SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private record OperationRow(
            long id,
            long portId,
            long newBagId,
            String status,
            String endReason,
            boolean firstUnlockMayHaveExecuted,
            LocalDateTime createdAt,
            LocalDateTime offlineOccupancyReleasedAt) {
    }

    private record OccupancyRow(
            String kind,
            Long deliverySessionId,
            Long cleanOperationId) {

        private boolean matchesClean(long operationId) {
            return "CLEAN".equals(kind)
                    && deliverySessionId == null
                    && cleanOperationId != null
                    && cleanOperationId == operationId;
        }
    }
}
