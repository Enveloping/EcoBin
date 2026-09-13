package org.enveloping.ecobin.recycling.application.device;

import org.enveloping.ecobin.device.api.port.ExpiredUnstartedDeviceWorkPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/** Ends clean authorizations that never left the backend dispatch queue. */
@Service
public class ExpireUnstartedCleanService
        implements ExpiredUnstartedDeviceWorkPort {

    private final JdbcTemplate jdbc;

    public ExpireUnstartedCleanService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public List<Long> closeExpiredUnstartedWork(LocalDateTime now) {
        List<ExpiredClean> candidates = jdbc.query("""
                        SELECT
                            operation.id AS operation_id,
                            operation.tenant_id,
                            operation.organization_id,
                            operation.asset_id,
                            operation.port_id,
                            operation.new_bag_id,
                            operation.offline_occupancy_released_at,
                            command_row.id AS command_id
                        FROM rec_clean_operation operation
                        JOIN dev_device_command command_row
                          ON command_row.tenant_id = operation.tenant_id
                         AND command_row.organization_id =
                             operation.organization_id
                         AND command_row.asset_id =
                             operation.asset_id
                         AND command_row.clean_operation_id = operation.id
                         AND command_row.command_type =
                             'START_CLEAN_OPERATION'
                        JOIN ops_reliable_task task
                          ON task.source_device_command_id = command_row.id
                         AND task.task_type = 'START_CLEAN_OPERATION'
                        WHERE operation.status = 'PREPARED'
                          AND operation.start_authorization_expires_at <= ?
                          AND command_row.physical_state = 'QUEUED'
                          AND task.state = 'PENDING'
                          AND task.lease_token IS NULL
                          AND task.dispatch_wait_reason IN (
                              'DEVICE_OFFLINE',
                              'DEVICE_PRESENCE_UNKNOWN'
                          )
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ExpiredClean(
                        rs.getLong("operation_id"),
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("asset_id"),
                        rs.getLong("port_id"),
                        rs.getLong("new_bag_id"),
                        rs.getObject(
                                "offline_occupancy_released_at",
                                LocalDateTime.class),
                        rs.getLong("command_id")),
                now);
        List<Long> commandIds = new ArrayList<>(candidates.size());
        for (ExpiredClean candidate : candidates) {
            close(candidate, now);
            commandIds.add(candidate.commandId());
        }
        return List.copyOf(commandIds);
    }

    private void close(ExpiredClean candidate, LocalDateTime now) {
        requireMatchingOrReleasedOccupancy(candidate);
        requireSingle(jdbc.update("""
                        UPDATE rec_clean_operation
                        SET status = 'PRE_UNLOCK_ENDED',
                            ended_at = ?,
                            end_reason = 'START_AUTHORIZATION_EXPIRED',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status = 'PREPARED'
                        """,
                now,
                now,
                candidate.operationId(),
                candidate.tenantId(),
                candidate.organizationId(),
                candidate.assetId()),
                "expire clean authorization");
        requireOccupancyRelease(jdbc.update("""
                        DELETE FROM dev_device_occupancy
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND occupancy_kind = 'CLEAN'
                          AND clean_operation_id = ?
                        """,
                candidate.tenantId(),
                candidate.organizationId(),
                candidate.assetId(),
                candidate.operationId()),
                candidate.offlineOccupancyReleasedAt(),
                "release expired clean occupancy");
        int reservationReleased = jdbc.update("""
                        DELETE FROM rec_bag_current_occupancy
                        WHERE bag_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND occupancy_type = 'CLEAN_RESERVED'
                          AND clean_operation_id = ?
                        """,
                candidate.newBagId(),
                candidate.tenantId(),
                candidate.organizationId(),
                candidate.operationId());
        requireSingle(reservationReleased,
                "release expired clean bag reservation");
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
                candidate.tenantId(),
                candidate.organizationId(),
                candidate.newBagId(),
                candidate.portId(),
                candidate.operationId(),
                now,
                now),
                "record expired clean bag release");
        jdbc.update("""
                        UPDATE rec_clean_photo
                        SET status = 'PERMANENTLY_MISSING',
                            linked_at = ?,
                            missing_reason =
                                'START_AUTHORIZATION_EXPIRED',
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND clean_operation_id = ?
                          AND status = 'UPLOAD_PENDING'
                        """,
                now,
                now,
                candidate.tenantId(),
                candidate.organizationId(),
                candidate.operationId());
    }

    private void requireMatchingOrReleasedOccupancy(
            ExpiredClean candidate) {
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
                    && rows.getFirst().matchesClean(candidate.operationId())
                : rows.isEmpty();
        if (!valid) {
            throw new IllegalStateException(
                    "expired clean occupancy differs");
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

    private record ExpiredClean(
            long operationId,
            long tenantId,
            long organizationId,
            long assetId,
            long portId,
            long newBagId,
            LocalDateTime offlineOccupancyReleasedAt,
            long commandId) {
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
