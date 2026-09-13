package org.enveloping.ecobin.device.application.delivery;

import org.enveloping.ecobin.device.api.port.ExpiredUnstartedDeviceWorkPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

/** Ends delivery authorizations that were never dispatched to the edge. */
@Service
public class ExpireUnstartedDeliveryService
        implements ExpiredUnstartedDeviceWorkPort {

    private final JdbcTemplate jdbc;

    public ExpireUnstartedDeliveryService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public List<Long> closeExpiredUnstartedWork(LocalDateTime now) {
        List<ExpiredDelivery> candidates = jdbc.query("""
                        SELECT
                            session.id AS session_id,
                            session.tenant_id,
                            session.organization_id,
                            session.asset_id,
                            session.offline_occupancy_released_at,
                            command_row.id AS command_id
                        FROM dev_delivery_session session
                        JOIN dev_device_command command_row
                          ON command_row.tenant_id = session.tenant_id
                         AND command_row.organization_id =
                             session.organization_id
                         AND command_row.asset_id =
                             session.asset_id
                         AND command_row.delivery_session_id = session.id
                         AND command_row.command_type =
                             'START_DELIVERY_SESSION'
                        JOIN ops_reliable_task task
                          ON task.source_device_command_id = command_row.id
                         AND task.task_type = 'START_DELIVERY_SESSION'
                        WHERE session.status = 'AUTHORIZATION_QUEUED'
                          AND session.authorization_expires_at <= ?
                          AND command_row.physical_state = 'QUEUED'
                          AND task.state = 'PENDING'
                          AND task.lease_token IS NULL
                          AND task.dispatch_wait_reason IN (
                              'DEVICE_OFFLINE',
                              'DEVICE_PRESENCE_UNKNOWN'
                          )
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ExpiredDelivery(
                        rs.getLong("session_id"),
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("asset_id"),
                        rs.getObject(
                                "offline_occupancy_released_at",
                                LocalDateTime.class),
                        rs.getLong("command_id")),
                now);
        List<Long> commandIds = new ArrayList<>(candidates.size());
        for (ExpiredDelivery candidate : candidates) {
            requireMatchingOrReleasedOccupancy(candidate);
            int ended = jdbc.update("""
                            UPDATE dev_delivery_session
                            SET status = 'PRE_OPEN_ENDED',
                                ended_at = ?,
                                end_reason =
                                    'START_AUTHORIZATION_EXPIRED',
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE id = ?
                              AND tenant_id = ?
                              AND organization_id = ?
                              AND asset_id = ?
                              AND status = 'AUTHORIZATION_QUEUED'
                            """,
                    now,
                    now,
                    candidate.sessionId(),
                    candidate.tenantId(),
                    candidate.organizationId(),
                    candidate.assetId());
            requireSingle(ended, "expire delivery authorization");
            requireOccupancyRelease(jdbc.update("""
                            DELETE FROM dev_device_occupancy
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND asset_id = ?
                              AND occupancy_kind = 'DELIVERY'
                              AND delivery_session_id = ?
                            """,
                    candidate.tenantId(),
                    candidate.organizationId(),
                    candidate.assetId(),
                    candidate.sessionId()),
                    candidate.offlineOccupancyReleasedAt(),
                    "release expired delivery occupancy");
            commandIds.add(candidate.commandId());
        }
        return List.copyOf(commandIds);
    }

    private void requireMatchingOrReleasedOccupancy(
            ExpiredDelivery candidate) {
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
                candidate.tenantId(),
                candidate.organizationId(),
                candidate.assetId());
        boolean valid = candidate.offlineOccupancyReleasedAt() == null
                ? rows.size() == 1
                    && rows.getFirst().matchesDelivery(candidate.sessionId())
                : rows.isEmpty();
        if (!valid) {
            throw new IllegalStateException(
                    "expired delivery occupancy differs");
        }
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

    private record ExpiredDelivery(
            long sessionId,
            long tenantId,
            long organizationId,
            long assetId,
            LocalDateTime offlineOccupancyReleasedAt,
            long commandId) {
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
