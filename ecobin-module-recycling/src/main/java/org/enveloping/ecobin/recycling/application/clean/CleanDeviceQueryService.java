package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.StartCleanIdentityParticipationPort;
import org.enveloping.ecobin.identity.api.result.LockedCleanOrganizationUser;
import org.enveloping.ecobin.identity.api.result.LockedMiniappCleanScope;
import org.enveloping.ecobin.recycling.web.v1.CleanDeviceModels.CleanDeviceItem;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.CursorPage;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** 当前清运员所在机构的设备工作列表。 */
@Service
public class CleanDeviceQueryService {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;

    private final JdbcTemplate jdbc;
    private final NamedParameterJdbcTemplate namedJdbc;
    private final StartCleanIdentityParticipationPort identity;
    private final CleanRecordCursorCodec cursorCodec;

    public CleanDeviceQueryService(
            JdbcTemplate jdbc,
            NamedParameterJdbcTemplate namedJdbc,
            StartCleanIdentityParticipationPort identity,
            CleanRecordCursorCodec cursorCodec) {
        this.jdbc = jdbc;
        this.namedJdbc = namedJdbc;
        this.identity = identity;
        this.cursorCodec = cursorCodec;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public CursorPage<CleanDeviceItem> devices(
            String requestedFilter,
            String cursor,
            Integer requestedLimit) {
        DeviceFilter filter = DeviceFilter.parse(requestedFilter);
        int limit = limit(requestedLimit);
        LockedMiniappCleanScope lockedScope =
                identity.lockCurrentMiniappScope();
        ScopeIds scope = lockedScope.organizationScopeRef()
                .withOrganizationScopeOnce(ScopeIds::new);
        LockedCleanOrganizationUser cleaner =
                identity.lockCurrentCleaner(lockedScope);
        return cleaner.organizationUserRef().withOrganizationUserOnce(
                (tenantId, organizationId, ignoredUserId) -> {
                    requireScope(scope, tenantId, organizationId);
                    String fingerprint = fingerprint(
                            tenantId, organizationId, filter, limit);
                    PageAnchor anchor = cursor == null
                            || cursor.isBlank()
                            ? firstAnchor(tenantId, organizationId)
                            : decode(cursor, fingerprint);
                    PageRows rows = query(
                            tenantId,
                            organizationId,
                            filter,
                            anchor,
                            fingerprint,
                            limit);
                    return new CursorPage<>(
                            rows.items(),
                            instant(anchor.asOf()),
                            rows.nextCursor());
                });
    }

    private PageAnchor firstAnchor(
            long tenantId,
            long organizationId) {
        LocalDateTime asOf = databaseNow();
        Long highWatermark = jdbc.queryForObject("""
                SELECT COALESCE(MAX(id), 0)
                FROM dev_device_asset
                WHERE tenant_id = ?
                  AND organization_id = ?
                """, Long.class, tenantId, organizationId);
        return new PageAnchor(
                highWatermark == null ? 0 : highWatermark,
                asOf,
                Long.MAX_VALUE);
    }

    private PageAnchor decode(
            String cursor,
            String fingerprint) {
        CleanRecordCursorCodec.DecodedCursor decoded =
                cursorCodec.decode(cursor, fingerprint);
        try {
            long lastId = Long.parseLong(decoded.lastStableKey());
            if (lastId <= 0) {
                throw new NumberFormatException();
            }
            return new PageAnchor(
                    decoded.highWatermark(),
                    decoded.lastSortTime(),
                    lastId);
        } catch (NumberFormatException invalid) {
            throw invalidCursor();
        }
    }

    private PageRows query(
            long tenantId,
            long organizationId,
            DeviceFilter filter,
            PageAnchor anchor,
            String fingerprint,
            int limit) {
        StringBuilder sql = new StringBuilder("""
                SELECT asset.id,
                       asset.device_public_code,
                       asset.installation_display_name AS display_name,
                       asset.installation_address AS address,
                       COALESCE(transport.onenet_connection_status, 'UNKNOWN')
                           AS connection_status,
                       (
                           SELECT COUNT(*)
                           FROM dev_port counted_port
                           WHERE counted_port.tenant_id = asset.tenant_id
                             AND counted_port.organization_id =
                                 asset.organization_id
                             AND counted_port.asset_id = asset.id
                       ) AS port_count,
                       (
                           SELECT MAX(delivery.backend_received_at)
                           FROM rec_delivery_order delivery
                           WHERE delivery.tenant_id = asset.tenant_id
                             AND delivery.organization_id =
                                 asset.organization_id
                             AND delivery.asset_id = asset.id
                       ) AS last_delivery_at,
                       (
                           SELECT MAX(clean.completed_at)
                           FROM rec_clean_record clean
                           WHERE clean.tenant_id = asset.tenant_id
                             AND clean.organization_id =
                                 asset.organization_id
                             AND clean.asset_id = asset.id
                       ) AS last_clean_at,
                       (
                           SELECT COUNT(*)
                           FROM rec_fullness_event fullness
                           JOIN dev_port full_port
                             ON full_port.id = fullness.port_id
                           WHERE fullness.tenant_id = asset.tenant_id
                             AND fullness.organization_id =
                                 asset.organization_id
                             AND fullness.status = 'ACTIVE'
                             AND full_port.asset_id = asset.id
                       ) AS full_port_count,
                       (
                           SELECT MIN(fullness.confirmed_at)
                           FROM rec_fullness_event fullness
                           JOIN dev_port full_port
                             ON full_port.id = fullness.port_id
                           WHERE fullness.tenant_id = asset.tenant_id
                             AND fullness.organization_id =
                                 asset.organization_id
                             AND fullness.status = 'ACTIVE'
                             AND full_port.asset_id = asset.id
                       ) AS oldest_full_since
                FROM dev_device_asset asset
                JOIN iam_tenant tenant
                  ON tenant.id = asset.tenant_id
                 AND tenant.status = 'ENABLED'
                JOIN iam_organization organization
                  ON organization.tenant_id = asset.tenant_id
                 AND organization.id = asset.organization_id
                 AND organization.status = 'ENABLED'
                LEFT JOIN dev_device_transport_state transport
                  ON transport.asset_id = asset.id
                WHERE asset.tenant_id = :tenantId
                  AND asset.organization_id = :organizationId
                  AND asset.lifecycle_status = 'NORMAL'
                  AND asset.acceptance_status = 'PASSED'
                  AND asset.id <= :highWatermark
                  AND asset.id < :lastId
                """);
        MapSqlParameterSource parameters = new MapSqlParameterSource()
                .addValue("tenantId", tenantId)
                .addValue("organizationId", organizationId)
                .addValue("highWatermark", anchor.highWatermark())
                .addValue("lastId", anchor.lastId())
                .addValue("deliveryThreshold", anchor.asOf().minusHours(24))
                .addValue("cleanThreshold", anchor.asOf().minusHours(24))
                .addValue("fullThreshold", anchor.asOf().minusHours(2))
                .addValue("limit", limit + 1);
        filter.append(sql);
        sql.append(" ORDER BY asset.id DESC LIMIT :limit");
        List<DeviceRow> fetched = namedJdbc.query(
                sql.toString(), parameters,
                (rs, ignored) -> row(rs));
        boolean hasMore = fetched.size() > limit;
        List<DeviceRow> rows = hasMore
                ? List.copyOf(fetched.subList(0, limit))
                : List.copyOf(fetched);
        String nextCursor = null;
        if (hasMore && !rows.isEmpty()) {
            nextCursor = cursorCodec.encode(
                    anchor.highWatermark(),
                    anchor.asOf(),
                    Long.toString(rows.getLast().id()),
                    fingerprint);
        }
        return new PageRows(
                rows.stream().map(DeviceRow::item).toList(),
                nextCursor);
    }

    private static DeviceRow row(ResultSet rs) throws SQLException {
        return new DeviceRow(
                rs.getLong("id"),
                new CleanDeviceItem(
                        rs.getString("device_public_code"),
                        rs.getString("display_name"),
                        rs.getString("address"),
                        rs.getString("connection_status"),
                        rs.getInt("port_count"),
                        instant(rs.getObject(
                                "last_delivery_at", LocalDateTime.class)),
                        instant(rs.getObject(
                                "last_clean_at", LocalDateTime.class)),
                        rs.getInt("full_port_count"),
                        instant(rs.getObject(
                                "oldest_full_since", LocalDateTime.class))));
    }

    private LocalDateTime databaseNow() {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (now == null) {
            throw new IllegalStateException(
                    "database time is unavailable");
        }
        return now;
    }

    private static int limit(Integer value) {
        if (value == null) {
            return DEFAULT_LIMIT;
        }
        if (value < 1 || value > MAX_LIMIT) {
            throw new TargetApiException(
                    400,
                    "COMMON.VALIDATION_FAILED",
                    "limit 必须在 1 到 100 之间",
                    false,
                    Map.of("field", "limit"));
        }
        return value;
    }

    private static String fingerprint(
            long tenantId,
            long organizationId,
            DeviceFilter filter,
            int limit) {
        String value = tenantId + "|" + organizationId + "|"
                + filter.name() + "|" + limit;
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(
                            value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", exception);
        }
    }

    private static void requireScope(
            ScopeIds expected,
            long tenantId,
            long organizationId) {
        if (expected.tenantId() != tenantId
                || expected.organizationId() != organizationId) {
            throw new IllegalStateException(
                    "clean device scope changed after authorization");
        }
    }

    private static Instant instant(LocalDateTime value) {
        return value == null
                ? null
                : value.toInstant(ZoneOffset.UTC);
    }

    private static TargetApiException invalidCursor() {
        return new TargetApiException(
                400,
                "COMMON.INVALID_CURSOR",
                "清运设备分页游标无效或已过期");
    }

    enum DeviceFilter {
        ALL,
        ONLINE,
        NO_DELIVERY_24H,
        NO_CLEAN_24H,
        FULL,
        FULL_TIMEOUT_2H;

        static DeviceFilter parse(String value) {
            if (value == null || value.isBlank()) {
                throw validation();
            }
            try {
                return valueOf(value.trim().toUpperCase(Locale.ROOT));
            } catch (IllegalArgumentException invalid) {
                throw validation();
            }
        }

        void append(StringBuilder sql) {
            switch (this) {
                case ALL -> {
                }
                case ONLINE -> sql.append("""
                         AND EXISTS (
                             SELECT 1
                             FROM dev_device_transport_state online
                             WHERE online.asset_id = asset.id
                               AND online.onenet_connection_status = 'ONLINE'
                         )
                        """);
                case NO_DELIVERY_24H -> sql.append("""
                         AND NOT EXISTS (
                             SELECT 1
                             FROM rec_delivery_order recent_delivery
                             WHERE recent_delivery.tenant_id = asset.tenant_id
                               AND recent_delivery.organization_id =
                                   asset.organization_id
                               AND recent_delivery.asset_id = asset.id
                               AND recent_delivery.backend_received_at >=
                                   :deliveryThreshold
                         )
                        """);
                case NO_CLEAN_24H -> sql.append("""
                         AND NOT EXISTS (
                             SELECT 1
                             FROM rec_clean_record recent_clean
                             WHERE recent_clean.tenant_id = asset.tenant_id
                               AND recent_clean.organization_id =
                                   asset.organization_id
                               AND recent_clean.asset_id = asset.id
                               AND recent_clean.completed_at >= :cleanThreshold
                         )
                        """);
                case FULL -> appendFull(sql, false);
                case FULL_TIMEOUT_2H -> appendFull(sql, true);
            }
        }

        private static void appendFull(
                StringBuilder sql,
                boolean timedOut) {
            sql.append("""
                     AND EXISTS (
                         SELECT 1
                         FROM rec_fullness_event current_full
                         JOIN dev_port current_full_port
                           ON current_full_port.id = current_full.port_id
                         WHERE current_full.tenant_id = asset.tenant_id
                           AND current_full.organization_id =
                               asset.organization_id
                           AND current_full.status = 'ACTIVE'
                           AND current_full_port.asset_id = asset.id
                    """);
            if (timedOut) {
                sql.append(" AND current_full.confirmed_at <= :fullThreshold");
            }
            sql.append(" )");
        }

        private static TargetApiException validation() {
            return new TargetApiException(
                    400,
                    "COMMON.VALIDATION_FAILED",
                    "filter 取值无效",
                    false,
                    Map.of("field", "filter"));
        }
    }

    private record ScopeIds(long tenantId, long organizationId) {
    }

    private record PageAnchor(
            long highWatermark,
            LocalDateTime asOf,
            long lastId) {
    }

    private record DeviceRow(long id, CleanDeviceItem item) {
    }

    private record PageRows(
            List<CleanDeviceItem> items,
            String nextCursor) {
    }
}
