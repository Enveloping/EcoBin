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
                               first_unlock_may_have_executed,
                               created_at
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
                        rs.getBoolean(
                                "first_unlock_may_have_executed"),
                        rs.getObject(
                                "created_at", LocalDateTime.class)),
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
        requireSingle(jdbc.update("""
                        UPDATE rec_clean_operation
                        SET status = 'RECOVERY_REQUIRED',
                            edge_saved_confirmed = 1,
                            first_unlock_may_have_executed = 1,
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
                operation.id(),
                observation.tenantId(),
                observation.organizationId(),
                observation.assetId()),
                "project uncertain clean failure");
    }

    private void endBeforeUnlock(
            TrustedCleanCommandObservation observation,
            OperationRow operation) {
        String reason = observation.errorCode();
        if (reason == null || reason.isBlank() || reason.length() > 64) {
            throw new IllegalArgumentException(
                    "pre-unlock clean failure requires a stable error code");
        }
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
        requireSingle(jdbc.update("""
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

    static LocalDateTime trustedOperationTime(
            LocalDateTime occurredAt,
            LocalDateTime createdAt,
            LocalDateTime receivedAt) {
        return occurredAt != null
                && !occurredAt.isBefore(createdAt)
                && !occurredAt.isAfter(receivedAt)
                ? occurredAt
                : receivedAt;
    }

    private static void requireSingle(int updated, String action) {
        if (updated != 1) {
            throw new IllegalStateException(
                    action + " expected one row but updated " + updated);
        }
    }

    private record OperationRow(
            long id,
            long portId,
            long newBagId,
            String status,
            boolean firstUnlockMayHaveExecuted,
            LocalDateTime createdAt) {
    }
}
