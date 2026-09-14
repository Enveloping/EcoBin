package org.enveloping.ecobin.device.application.delivery;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;

/** Projects trusted edge command evidence into the delivery state machine. */
@Service
public class ApplyDeliveryCommandObservationService {

    private final JdbcTemplate jdbc;

    public ApplyDeliveryCommandObservationService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void apply(
            long tenantId,
            long organizationId,
            long assetId,
            long deliverySessionId,
            String commandType,
            String stage,
            String errorCode,
            LocalDateTime occurredAt,
            LocalDateTime receivedAt) {
        apply(
                tenantId,
                organizationId,
                assetId,
                deliverySessionId,
                commandType,
                stage,
                errorCode,
                occurredAt,
                receivedAt,
                receivedAt);
    }

    @Transactional(propagation = Propagation.MANDATORY)
    void apply(
            long tenantId,
            long organizationId,
            long assetId,
            long deliverySessionId,
            String commandType,
            String stage,
            String errorCode,
            LocalDateTime occurredAt,
            LocalDateTime receivedAt,
            LocalDateTime projectedAt) {
        if (!"START_DELIVERY_SESSION".equals(commandType)) {
            return;
        }
        List<SessionRow> rows = jdbc.query("""
                        SELECT id, status, first_edge_accepted_at, created_at,
                               offline_occupancy_released_at
                        FROM dev_delivery_session
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new SessionRow(
                        rs.getLong("id"),
                        rs.getString("status"),
                        rs.getObject(
                                "first_edge_accepted_at",
                                LocalDateTime.class),
                        rs.getObject("created_at", LocalDateTime.class),
                        rs.getObject(
                                "offline_occupancy_released_at",
                                LocalDateTime.class)),
                deliverySessionId,
                tenantId,
                organizationId,
                assetId);
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "trusted delivery command target is missing");
        }
        SessionRow session = rows.getFirst();
        var action = DeliveryCommandObservationDecision.decide(
                commandType, session.status(), stage, errorCode);
        LocalDateTime effectiveAt = trustedOperationTime(
                occurredAt, session.createdAt(), receivedAt);
        switch (action) {
            case MARK_EDGE_ACCEPTED -> markEdgeAccepted(
                    tenantId, organizationId, assetId,
                    session, effectiveAt, projectedAt);
            case MARK_IN_PROGRESS -> markInProgress(
                    tenantId, organizationId, assetId,
                    session, effectiveAt, projectedAt);
            case END_BEFORE_OPEN -> endBeforeOpen(
                    tenantId, organizationId, assetId,
                    session, errorCode, projectedAt);
            case ABORT_TERMINAL_RESULT -> abortValueFreeFailure(
                    tenantId, organizationId, assetId,
                    session, errorCode, projectedAt);
            case ABORT_NATIVE_CONTROL_FAILURE ->
                    abortNativeControlFailure(
                            tenantId, organizationId, assetId,
                            session, errorCode, projectedAt);
            case REQUIRE_RECOVERY -> requireRecovery(
                    tenantId, organizationId, assetId,
                    session, projectedAt);
            case NONE -> {
                // Duplicate, stale and terminal observations remain evidence.
            }
        }
    }

    private void markEdgeAccepted(
            long tenantId,
            long organizationId,
            long assetId,
            SessionRow session,
            LocalDateTime effectiveAt,
            LocalDateTime receivedAt) {
        requireSingle(jdbc.update("""
                        UPDATE dev_delivery_session
                        SET first_edge_accepted_at = CASE
                                WHEN first_edge_accepted_at IS NULL
                                  OR first_edge_accepted_at > ?
                                THEN ?
                                ELSE first_edge_accepted_at
                            END,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status IN (
                              'AUTHORIZATION_QUEUED',
                              'IN_PROGRESS',
                              'RESULT_PENDING_RECOVERY'
                          )
                        """,
                effectiveAt,
                effectiveAt,
                receivedAt,
                session.id(),
                tenantId,
                organizationId,
                assetId),
                "project delivery edge acceptance");
    }

    private void markInProgress(
            long tenantId,
            long organizationId,
            long assetId,
            SessionRow session,
            LocalDateTime effectiveAt,
            LocalDateTime projectedAt) {
        requireSingle(jdbc.update("""
                        UPDATE dev_delivery_session
                        SET status = CASE
                                WHEN status = 'AUTHORIZATION_QUEUED'
                                THEN 'IN_PROGRESS'
                                ELSE status
                            END,
                            first_edge_accepted_at = CASE
                                WHEN first_edge_accepted_at IS NULL
                                  OR first_edge_accepted_at > ?
                                THEN ?
                                ELSE first_edge_accepted_at
                            END,
                            first_physical_progress_at = CASE
                                WHEN first_physical_progress_at IS NULL
                                  OR first_physical_progress_at > ?
                                THEN ?
                                ELSE first_physical_progress_at
                            END,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status IN (
                              'AUTHORIZATION_QUEUED',
                              'IN_PROGRESS',
                              'RESULT_PENDING_RECOVERY'
                          )
                        """,
                effectiveAt,
                effectiveAt,
                effectiveAt,
                effectiveAt,
                projectedAt,
                session.id(),
                tenantId,
                organizationId,
                assetId),
                "project delivery physical start");
    }

    private void endBeforeOpen(
            long tenantId,
            long organizationId,
            long assetId,
            SessionRow session,
            String errorCode,
            LocalDateTime receivedAt) {
        String reason = stableReason(errorCode);
        requireMatchingOrReleasedOccupancy(
                tenantId, organizationId, assetId, session);
        requireSingle(jdbc.update("""
                        UPDATE dev_delivery_session
                        SET status = 'PRE_OPEN_ENDED',
                            ended_at = ?,
                            end_reason = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status = 'AUTHORIZATION_QUEUED'
                          AND first_physical_progress_at IS NULL
                        """,
                receivedAt,
                reason,
                receivedAt,
                session.id(),
                tenantId,
                organizationId,
                assetId),
                "end delivery before open");
        requireOccupancyRelease(jdbc.update("""
                        DELETE FROM dev_device_occupancy
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND occupancy_kind = 'DELIVERY'
                          AND delivery_session_id = ?
                        """,
                tenantId,
                organizationId,
                assetId,
                session.id()),
                session.offlineOccupancyReleasedAt(),
                "release pre-open delivery occupancy");
    }

    private void requireMatchingOrReleasedOccupancy(
            long tenantId,
            long organizationId,
            long assetId,
            SessionRow session) {
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
                tenantId,
                organizationId,
                assetId);
        boolean valid = session.offlineOccupancyReleasedAt() == null
                ? rows.size() == 1
                    && rows.getFirst().matchesDelivery(session.id())
                : rows.isEmpty();
        if (!valid) {
            throw new IllegalStateException(
                    "trusted delivery command occupancy differs");
        }
    }

    private void requireRecovery(
            long tenantId,
            long organizationId,
            long assetId,
            SessionRow session,
            LocalDateTime receivedAt) {
        requireSingle(jdbc.update("""
                        UPDATE dev_delivery_session
                        SET status = 'RESULT_PENDING_RECOVERY',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status IN (
                              'AUTHORIZATION_QUEUED',
                              'IN_PROGRESS',
                              'RESULT_PENDING_RECOVERY'
                          )
                        """,
                receivedAt,
                session.id(),
                tenantId,
                organizationId,
                assetId),
                "project uncertain delivery failure");
    }

    private void abortValueFreeFailure(
            long tenantId,
            long organizationId,
            long assetId,
            SessionRow session,
            String errorCode,
            LocalDateTime receivedAt) {
        String reason = stableReason(errorCode);
        requireMatchingOrReleasedOccupancy(
                tenantId, organizationId, assetId, session);
        requireSingle(jdbc.update("""
                        UPDATE dev_delivery_session
                        SET status = 'DEVICE_ABORTED',
                            ended_at = ?,
                            end_reason = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status IN (
                              'AUTHORIZATION_QUEUED',
                              'IN_PROGRESS',
                              'RESULT_PENDING_RECOVERY'
                          )
                        """,
                receivedAt,
                reason,
                receivedAt,
                session.id(),
                tenantId,
                organizationId,
                assetId),
                "abort delivery from explicit value-free device failure");
        requireOccupancyRelease(jdbc.update("""
                        DELETE FROM dev_device_occupancy
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND occupancy_kind = 'DELIVERY'
                          AND delivery_session_id = ?
                        """,
                tenantId,
                organizationId,
                assetId,
                session.id()),
                session.offlineOccupancyReleasedAt(),
                "release value-free failed delivery occupancy");
    }

    private void abortNativeControlFailure(
            long tenantId,
            long organizationId,
            long assetId,
            SessionRow session,
            String errorCode,
            LocalDateTime receivedAt) {
        if (!"MCU_COMMUNICATION_UNAVAILABLE".equals(errorCode)) {
            throw new IllegalArgumentException(
                    "native control failure requires its exact reason");
        }
        abortValueFreeFailure(
                tenantId,
                organizationId,
                assetId,
                session,
                errorCode,
                receivedAt);
    }

    static LocalDateTime trustedOperationTime(
            LocalDateTime occurredAt,
            LocalDateTime createdAt,
            LocalDateTime receivedAt) {
        // Device wall time remains raw evidence on the inbox event.  Business
        // transitions use the backend receive clock, so skew never changes
        // ordering or creates a cross-clock rejection boundary.
        return receivedAt;
    }

    private static String stableReason(String errorCode) {
        if (errorCode == null
                || errorCode.isBlank()
                || errorCode.length() > 64) {
            throw new IllegalArgumentException(
                    "pre-open delivery failure requires a stable error code");
        }
        return errorCode;
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

    private record SessionRow(
            long id,
            String status,
            LocalDateTime firstEdgeAcceptedAt,
            LocalDateTime createdAt,
            LocalDateTime offlineOccupancyReleasedAt) {
    }

    private record OccupancyRow(
            String kind,
            Long deliverySessionId,
            Long cleanOperationId) {

        private boolean matchesDelivery(long sessionId) {
            return "DELIVERY".equals(kind)
                    && deliverySessionId != null
                    && deliverySessionId == sessionId
                    && cleanOperationId == null;
        }
    }
}
