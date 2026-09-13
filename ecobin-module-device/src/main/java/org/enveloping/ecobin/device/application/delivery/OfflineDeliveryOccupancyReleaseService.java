package org.enveloping.ecobin.device.application.delivery;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Set;

/** Atomically marks and releases only the original delivery use-slot. */
@Service
public class OfflineDeliveryOccupancyReleaseService {

    static final long OFFLINE_RELEASE_SECONDS = 600L;
    private static final Set<String> ACTIVE_STATUSES = Set.of(
            "PREPARED",
            "AUTHORIZATION_QUEUED",
            "IN_PROGRESS",
            "RESULT_PENDING_RECOVERY");

    private final JdbcTemplate jdbc;

    public OfflineDeliveryOccupancyReleaseService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public boolean releaseIfEligible(long sessionId, long assetId) {
        requirePositive(sessionId, "sessionId");
        requirePositive(assetId, "assetId");
        if (jdbc.query("""
                        SELECT id
                        FROM dev_device_asset
                        WHERE id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                assetId).size() != 1) {
            return false;
        }
        List<Transport> transports = jdbc.query("""
                        SELECT onenet_connection_status, offline_since_at
                        FROM dev_device_transport_state
                        WHERE asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Transport(
                        rs.getString("onenet_connection_status"),
                        rs.getObject("offline_since_at", LocalDateTime.class)),
                assetId);
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (transports.size() != 1 || now == null
                || !eligible(transports.getFirst(), now)) {
            return false;
        }
        List<Session> sessions = jdbc.query("""
                        SELECT tenant_id, organization_id, status, ended_at,
                               offline_occupancy_released_at
                        FROM dev_delivery_session
                        WHERE id = ?
                          AND asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Session(
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getString("status"),
                        rs.getObject("ended_at", LocalDateTime.class),
                        rs.getObject(
                                "offline_occupancy_released_at",
                                LocalDateTime.class)),
                sessionId,
                assetId);
        if (sessions.size() != 1) {
            return false;
        }
        Session session = sessions.getFirst();
        if (!ACTIVE_STATUSES.contains(session.status())
                || session.endedAt() != null
                || session.releasedAt() != null) {
            return false;
        }
        List<Occupancy> occupancies = lockOccupancy(assetId);
        if (occupancies.size() != 1
                || !occupancies.getFirst().isDelivery(sessionId)) {
            throw invariant("original delivery occupancy differs");
        }
        requireSingle(jdbc.update("""
                        UPDATE dev_delivery_session
                        SET offline_occupancy_released_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND ended_at IS NULL
                          AND offline_occupancy_released_at IS NULL
                          AND status IN (
                              'PREPARED',
                              'AUTHORIZATION_QUEUED',
                              'IN_PROGRESS',
                              'RESULT_PENDING_RECOVERY'
                          )
                        """,
                now,
                now,
                sessionId,
                session.tenantId(),
                session.organizationId(),
                assetId),
                "mark delivery occupancy released");
        requireSingle(jdbc.update("""
                        DELETE FROM dev_device_occupancy
                        WHERE asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND occupancy_kind = 'DELIVERY'
                          AND delivery_session_id = ?
                        """,
                assetId,
                session.tenantId(),
                session.organizationId(),
                sessionId),
                "release original delivery occupancy");
        return true;
    }

    static boolean eligible(Transport transport, LocalDateTime now) {
        return "OFFLINE".equals(transport.status())
                && transport.offlineSinceAt() != null
                && transport.offlineSinceAt().isBefore(
                        now.minusSeconds(OFFLINE_RELEASE_SECONDS));
    }

    private List<Occupancy> lockOccupancy(long assetId) {
        return jdbc.query("""
                        SELECT occupancy_kind,
                               delivery_session_id,
                               clean_operation_id
                        FROM dev_device_occupancy
                        WHERE asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Occupancy(
                        rs.getString("occupancy_kind"),
                        nullableLong(rs, "delivery_session_id"),
                        nullableLong(rs, "clean_operation_id")),
                assetId);
    }

    private static Long nullableLong(
            java.sql.ResultSet rs,
            String column) throws java.sql.SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static void requirePositive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
    }

    private static void requireSingle(int affected, String action) {
        if (affected != 1) {
            throw invariant(action + " affected " + affected + " rows");
        }
    }

    private static IllegalStateException invariant(String message) {
        return new IllegalStateException(
                "offline delivery occupancy invariant failed: " + message);
    }

    record Transport(String status, LocalDateTime offlineSinceAt) {
    }

    private record Session(
            long tenantId,
            long organizationId,
            String status,
            LocalDateTime endedAt,
            LocalDateTime releasedAt) {
    }

    private record Occupancy(
            String kind,
            Long deliverySessionId,
            Long cleanOperationId) {

        private boolean isDelivery(long expectedSessionId) {
            return "DELIVERY".equals(kind)
                    && deliverySessionId != null
                    && deliverySessionId == expectedSessionId
                    && cleanOperationId == null;
        }
    }
}
