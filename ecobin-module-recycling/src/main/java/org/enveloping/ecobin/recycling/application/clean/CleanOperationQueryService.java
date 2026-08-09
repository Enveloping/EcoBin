package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.port.DeliveryOrderIdentityQueryPort;
import org.enveloping.ecobin.identity.api.port.DeliveryScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.DeliveryOrganizationUserFilterQuery;
import org.enveloping.ecobin.identity.api.query.DeliveryScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;
import org.enveloping.ecobin.identity.api.result.DeliveryOrderIdentityFacts;
import org.enveloping.ecobin.identity.api.value.DeliveryIdentityFactToken;
import org.enveloping.ecobin.recycling.web.v1.CleanOperationModels.WebCleanOperationDetail;
import org.enveloping.ecobin.recycling.web.v1.CleanOperationModels.WebCleanOperationItem;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.CursorPage;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/** Web 后台清运操作列表与详情查询。 */
@Service
public class CleanOperationQueryService {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;
    private static final Set<String> STATUSES = Set.of(
            "PREPARED",
            "EDGE_SAVED",
            "IN_PROGRESS",
            "RECOVERY_REQUIRED",
            "PRE_UNLOCK_ENDED",
            "COMPLETED",
            "ABORTED");

    private final JdbcTemplate jdbc;
    private final NamedParameterJdbcTemplate namedJdbc;
    private final DeliveryScopeAuthorizationPort authorization;
    private final DeliveryOrderIdentityQueryPort identityFacts;
    private final CleanOperationCursorCodec cursorCodec;
    private final ObjectMapper objectMapper;

    public CleanOperationQueryService(
            JdbcTemplate jdbc,
            NamedParameterJdbcTemplate namedJdbc,
            DeliveryScopeAuthorizationPort authorization,
            DeliveryOrderIdentityQueryPort identityFacts,
            CleanOperationCursorCodec cursorCodec,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.namedJdbc = namedJdbc;
        this.authorization = authorization;
        this.identityFacts = identityFacts;
        this.cursorCodec = cursorCodec;
        this.objectMapper = objectMapper;
    }

    @Transactional(readOnly = true)
    public CursorPage<WebCleanOperationItem> webOperations(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String cursor,
            Integer requestedLimit,
            String requestedStatus,
            UUID cleanerUserUid,
            String deviceCode,
            Integer portNo,
            Instant createdFrom,
            Instant createdTo) {
        AuthorizedDeliveryScope authorized = authorize(
                platformPath, tenantCode, organizationCode);
        int limit = limit(requestedLimit);
        Filters filters = Filters.validated(
                requestedStatus,
                deviceCode,
                portNo,
                createdFrom,
                createdTo);
        String fingerprint = fingerprint(filters.fingerprintFields(
                authorized, cleanerUserUid, limit));
        PageAnchor anchor = anchor(cursor, fingerprint);

        Optional<ResolvedUser> user = Optional.empty();
        if (cleanerUserUid != null) {
            var reference = identityFacts.resolveOrganizationUserFilter(
                    new DeliveryOrganizationUserFilterQuery(
                            authorized.tenantCode(),
                            authorized.organizationCode(),
                            new OrganizationUserUid(cleanerUserUid)));
            if (reference.isEmpty()) {
                authorized.persistenceRef().withScopeOnce(
                        (ignoredTenant,
                         ignoredOrganization,
                         ignoredPlatform,
                         ignoredStaff) -> null);
                return new CursorPage<>(List.of(), databaseNow(), null);
            }
            user = Optional.of(reference.orElseThrow()
                    .withOrganizationUserOnce(ResolvedUser::new));
        }
        ScopeIds scope = authorized.persistenceRef().withScopeOnce(
                (resolvedTenant,
                 resolvedOrganization,
                 ignoredPlatform,
                 ignoredStaff) -> new ScopeIds(
                        resolvedTenant, resolvedOrganization));
        Long cleanerId = null;
        if (user.isPresent()) {
            ResolvedUser resolved = user.orElseThrow();
            requireScope(scope, resolved.tenantId(),
                    resolved.organizationId());
            cleanerId = resolved.organizationUserId();
        }

        PageRows page = rows(
                scope,
                cleanerId,
                filters,
                anchor,
                fingerprint,
                limit);
        Map<Long, UUID> cleaners = cleaners(scope, page.rows());
        List<WebCleanOperationItem> items = page.rows().stream()
                .map(row -> item(row, required(
                        cleaners, row.cleanerId())))
                .toList();
        return new CursorPage<>(items, databaseNow(), page.nextCursor());
    }

    @Transactional(readOnly = true)
    public WebCleanOperationDetail webOperation(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            UUID operationUid) {
        AuthorizedDeliveryScope authorized = authorize(
                platformPath, tenantCode, organizationCode);
        ScopeIds scope = authorized.persistenceRef().withScopeOnce(
                (resolvedTenant,
                 resolvedOrganization,
                 ignoredPlatform,
                 ignoredStaff) -> new ScopeIds(
                        resolvedTenant, resolvedOrganization));
        OperationRow row = jdbc.query("""
                        SELECT operation.id, operation.operation_uid,
                               operation.cleaner_organization_user_id,
                               operation.status, operation.lock_version,
                               asset.device_public_code, port.port_no,
                               operation.old_bag_binding_state,
                               operation.old_bag_code_snapshot,
                               operation.new_bag_code_snapshot,
                               operation.pre_unlock_weight_status,
                               operation.pre_unlock_weight_g,
                               operation.pre_unlock_weight_fault_code,
                               operation.edge_saved_confirmed,
                               operation.first_unlock_may_have_executed,
                               operation.clean_lock_deenergized_confirmed,
                               operation.cleaner_physical_close_confirmed,
                               operation.start_authorization_expires_at,
                               operation.edge_saved_at,
                               operation.first_possible_unlock_at,
                               operation.solenoid_powered_off_at,
                               operation.cleaner_confirmed_closed_at,
                               operation.execution_deadline_at,
                               operation.reopen_count,
                               operation.recovery_count,
                               operation.created_at,
                               operation.updated_at,
                               operation.ended_at,
                               operation.end_reason,
                               record.clean_record_no
                        FROM rec_clean_operation operation
                        JOIN dev_device_asset asset
                          ON asset.id = operation.asset_id
                        JOIN dev_port port
                          ON port.id = operation.port_id
                        LEFT JOIN rec_clean_record record
                          ON record.id = operation.completion_record_id
                        WHERE operation.tenant_id = ?
                          AND operation.organization_id = ?
                          AND operation.operation_uid = ?
                        """,
                (rs, ignored) -> mapRow(rs),
                scope.tenantId(),
                scope.organizationId(),
                operationUid.toString()).stream().findFirst()
                .orElseThrow(CleanOperationQueryService::notFound);
        UUID cleanerUid = required(
                cleaners(scope, List.of(row)), row.cleanerId());
        return detail(row, cleanerUid);
    }

    private PageRows rows(
            ScopeIds scope,
            Long cleanerId,
            Filters filters,
            PageAnchor anchor,
            String fingerprint,
            int limit) {
        long highWatermark = anchor == null
                ? highWatermark(scope)
                : anchor.highWatermark();
        StringBuilder sql = new StringBuilder("""
                SELECT operation.id, operation.operation_uid,
                       operation.cleaner_organization_user_id,
                       operation.status, operation.lock_version,
                       asset.device_public_code, port.port_no,
                       operation.old_bag_binding_state,
                       operation.old_bag_code_snapshot,
                       operation.new_bag_code_snapshot,
                       operation.pre_unlock_weight_status,
                       operation.pre_unlock_weight_g,
                       operation.pre_unlock_weight_fault_code,
                       operation.edge_saved_confirmed,
                       operation.first_unlock_may_have_executed,
                       operation.clean_lock_deenergized_confirmed,
                       operation.cleaner_physical_close_confirmed,
                       operation.start_authorization_expires_at,
                       operation.edge_saved_at,
                       operation.first_possible_unlock_at,
                       operation.solenoid_powered_off_at,
                       operation.cleaner_confirmed_closed_at,
                       operation.execution_deadline_at,
                       operation.reopen_count,
                       operation.recovery_count,
                       operation.created_at,
                       operation.updated_at,
                       operation.ended_at,
                       operation.end_reason,
                       record.clean_record_no
                FROM rec_clean_operation operation
                JOIN dev_device_asset asset
                  ON asset.id = operation.asset_id
                JOIN dev_port port
                  ON port.id = operation.port_id
                LEFT JOIN rec_clean_record record
                  ON record.id = operation.completion_record_id
                WHERE operation.tenant_id = :tenantId
                  AND operation.organization_id = :organizationId
                  AND operation.id <= :highWatermark
                """);
        MapSqlParameterSource parameters = new MapSqlParameterSource()
                .addValue("tenantId", scope.tenantId())
                .addValue("organizationId", scope.organizationId())
                .addValue("highWatermark", highWatermark)
                .addValue("limit", limit + 1);
        if (cleanerId != null) {
            sql.append(" AND operation.cleaner_organization_user_id = :cleanerId");
            parameters.addValue("cleanerId", cleanerId);
        }
        filters.append(sql, parameters);
        if (anchor != null) {
            sql.append("""
                     AND (
                         operation.created_at < :lastCreatedAt
                         OR (
                             operation.created_at = :lastCreatedAt
                             AND operation.id < :lastId
                         )
                     )
                    """);
            parameters
                    .addValue("lastCreatedAt", anchor.lastCreatedAt())
                    .addValue("lastId", anchor.lastId());
        }
        sql.append("""
                 ORDER BY operation.created_at DESC, operation.id DESC
                 LIMIT :limit
                """);
        List<OperationRow> fetched = namedJdbc.query(
                sql.toString(), parameters, (rs, ignored) -> mapRow(rs));
        boolean hasMore = fetched.size() > limit;
        List<OperationRow> result = hasMore
                ? List.copyOf(fetched.subList(0, limit))
                : List.copyOf(fetched);
        String nextCursor = null;
        if (hasMore && !result.isEmpty()) {
            OperationRow last = result.getLast();
            nextCursor = cursorCodec.encode(
                    highWatermark,
                    last.createdAt(),
                    last.id(),
                    fingerprint);
        }
        return new PageRows(result, nextCursor);
    }

    private Map<Long, UUID> cleaners(
            ScopeIds scope,
            List<OperationRow> rows) {
        if (rows.isEmpty()) {
            return Map.of();
        }
        Map<Long, DeliveryIdentityFactToken> tokens =
                new LinkedHashMap<>();
        rows.forEach(row -> tokens.computeIfAbsent(
                row.cleanerId(),
                ignored -> DeliveryIdentityFactToken.create()));
        List<CleanRecordIdentityBatchRef.UserEntry> entries =
                tokens.entrySet().stream()
                        .map(entry ->
                                new CleanRecordIdentityBatchRef.UserEntry(
                                        entry.getValue(),
                                        scope.tenantId(),
                                        scope.organizationId(),
                                        entry.getKey()))
                        .toList();
        DeliveryOrderIdentityFacts facts = identityFacts.resolveFacts(
                CleanRecordIdentityBatchRef.issue(entries, List.of()));
        Map<Long, UUID> result = new HashMap<>();
        tokens.forEach((id, token) -> {
            OrganizationUserUid uid = facts.organizationUsers().get(token);
            if (uid == null) {
                throw new IllegalStateException(
                        "cleaner identity is incomplete");
            }
            result.put(id, uid.value());
        });
        return Map.copyOf(result);
    }

    private AuthorizedDeliveryScope authorize(
            boolean platformPath,
            String tenantCode,
            String organizationCode) {
        AuthorizedDeliveryScope result = authorization.authorize(
                new DeliveryScopeAuthorizationQuery(
                        platformPath, tenantCode, organizationCode));
        if (!result.cleanRead()) {
            throw new TargetApiException(
                    403,
                    "AUTH.CAPABILITY_REQUIRED",
                    "当前账号缺少清运记录读取能力");
        }
        return result;
    }

    private long highWatermark(ScopeIds scope) {
        Long value = jdbc.queryForObject("""
                        SELECT COALESCE(MAX(id), 0)
                        FROM rec_clean_operation
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        """,
                Long.class,
                scope.tenantId(),
                scope.organizationId());
        return value == null ? 0 : value;
    }

    private Instant databaseNow() {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (now == null) {
            throw new IllegalStateException(
                    "database time is unavailable");
        }
        return instant(now);
    }

    private String fingerprint(Map<String, ?> fields) {
        try {
            byte[] canonical = objectMapper.writeValueAsBytes(fields);
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256")
                            .digest(canonical));
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "clean operation filter cannot be fingerprinted",
                    exception);
        }
    }

    private PageAnchor anchor(String cursor, String fingerprint) {
        if (cursor == null || cursor.isBlank()) {
            return null;
        }
        var decoded = cursorCodec.decode(cursor, fingerprint);
        return new PageAnchor(
                decoded.highWatermark(),
                decoded.lastCreatedAt(),
                decoded.lastId());
    }

    private static OperationRow mapRow(java.sql.ResultSet rs)
            throws java.sql.SQLException {
        return new OperationRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("operation_uid")),
                rs.getLong("cleaner_organization_user_id"),
                rs.getString("status"),
                rs.getLong("lock_version"),
                rs.getString("device_public_code"),
                rs.getInt("port_no"),
                rs.getString("old_bag_binding_state"),
                rs.getString("old_bag_code_snapshot"),
                rs.getString("new_bag_code_snapshot"),
                rs.getString("pre_unlock_weight_status"),
                nullableLong(rs.getObject("pre_unlock_weight_g")),
                rs.getString("pre_unlock_weight_fault_code"),
                rs.getBoolean("edge_saved_confirmed"),
                rs.getBoolean("first_unlock_may_have_executed"),
                rs.getBoolean("clean_lock_deenergized_confirmed"),
                rs.getBoolean("cleaner_physical_close_confirmed"),
                rs.getObject("start_authorization_expires_at",
                        LocalDateTime.class),
                rs.getObject("edge_saved_at", LocalDateTime.class),
                rs.getObject("first_possible_unlock_at",
                        LocalDateTime.class),
                rs.getObject("solenoid_powered_off_at",
                        LocalDateTime.class),
                rs.getObject("cleaner_confirmed_closed_at",
                        LocalDateTime.class),
                rs.getObject("execution_deadline_at",
                        LocalDateTime.class),
                rs.getInt("reopen_count"),
                rs.getInt("recovery_count"),
                rs.getObject("created_at", LocalDateTime.class),
                rs.getObject("updated_at", LocalDateTime.class),
                rs.getObject("ended_at", LocalDateTime.class),
                rs.getString("end_reason"),
                rs.getString("clean_record_no"));
    }

    private static WebCleanOperationItem item(
            OperationRow row,
            UUID cleanerUid) {
        return new WebCleanOperationItem(
                row.operationUid(), row.status(), row.version(), cleanerUid,
                row.deviceCode(), row.portNo(), row.oldBagBindingState(),
                row.removedBagQr(), row.installedBagQr(),
                row.edgeSavedConfirmed(),
                row.firstUnlockMayHaveExecuted(),
                row.cleanLockDeenergizedConfirmed(),
                row.cleanerPhysicalCloseConfirmed(),
                instant(row.startAuthorizationExpiresAt()),
                instant(row.executionDeadlineAt()),
                instant(row.createdAt()), instant(row.updatedAt()),
                instant(row.endedAt()), row.endReason(),
                row.cleanRecordNo());
    }

    private static WebCleanOperationDetail detail(
            OperationRow row,
            UUID cleanerUid) {
        return new WebCleanOperationDetail(
                row.operationUid(), row.status(), row.version(), cleanerUid,
                row.deviceCode(), row.portNo(), row.oldBagBindingState(),
                row.removedBagQr(), row.installedBagQr(),
                row.preUnlockWeightStatus(), kg(row.preUnlockWeightG()),
                row.preUnlockWeightFaultCode(),
                row.edgeSavedConfirmed(),
                row.firstUnlockMayHaveExecuted(),
                row.cleanLockDeenergizedConfirmed(),
                row.cleanerPhysicalCloseConfirmed(),
                instant(row.startAuthorizationExpiresAt()),
                instant(row.edgeSavedAt()),
                instant(row.firstPossibleUnlockAt()),
                instant(row.solenoidPoweredOffAt()),
                instant(row.cleanerConfirmedClosedAt()),
                instant(row.executionDeadlineAt()),
                row.reopenCount(), row.recoveryCount(),
                instant(row.createdAt()), instant(row.updatedAt()),
                instant(row.endedAt()), row.endReason(),
                row.cleanRecordNo());
    }

    private static int limit(Integer value) {
        if (value == null) {
            return DEFAULT_LIMIT;
        }
        if (value < 1 || value > MAX_LIMIT) {
            throw validation("limit", "每页数量必须在 1 到 100 之间");
        }
        return value;
    }

    private static String text(
            String value,
            String field,
            int maxLength) {
        if (value == null) {
            return null;
        }
        String result = value.trim();
        if (result.isEmpty() || result.length() > maxLength) {
            throw validation(field, field + " 格式不正确");
        }
        return result;
    }

    private static void requireScope(
            ScopeIds expected,
            long tenantId,
            long organizationId) {
        if (expected.tenantId() != tenantId
                || expected.organizationId() != organizationId) {
            throw new IllegalStateException(
                    "cleaner filter resolved outside authorized scope");
        }
    }

    private static UUID required(Map<Long, UUID> values, long id) {
        UUID value = values.get(id);
        if (value == null) {
            throw new IllegalStateException(
                    "cleaner identity is incomplete");
        }
        return value;
    }

    private static Long nullableLong(Object value) {
        return value == null ? null : ((Number) value).longValue();
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static LocalDateTime time(Instant value) {
        return value == null
                ? null
                : LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }

    private static String kg(Long grams) {
        if (grams == null) {
            return null;
        }
        BigDecimal value = BigDecimal.valueOf(grams, 3)
                .stripTrailingZeros();
        if (value.scale() < 2) {
            value = value.setScale(2);
        }
        return value.toPlainString();
    }

    private static TargetApiException validation(
            String field,
            String message) {
        return new TargetApiException(
                400,
                "COMMON.VALIDATION_FAILED",
                message,
                false,
                Map.of("field", field));
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "资源不存在");
    }

    private record ScopeIds(long tenantId, long organizationId) {
    }

    private record ResolvedUser(
            long tenantId,
            long organizationId,
            long organizationUserId) {
    }

    private record PageAnchor(
            long highWatermark,
            LocalDateTime lastCreatedAt,
            long lastId) {
    }

    private record PageRows(
            List<OperationRow> rows,
            String nextCursor) {
    }

    private record OperationRow(
            long id,
            UUID operationUid,
            long cleanerId,
            String status,
            long version,
            String deviceCode,
            int portNo,
            String oldBagBindingState,
            String removedBagQr,
            String installedBagQr,
            String preUnlockWeightStatus,
            Long preUnlockWeightG,
            String preUnlockWeightFaultCode,
            boolean edgeSavedConfirmed,
            boolean firstUnlockMayHaveExecuted,
            boolean cleanLockDeenergizedConfirmed,
            boolean cleanerPhysicalCloseConfirmed,
            LocalDateTime startAuthorizationExpiresAt,
            LocalDateTime edgeSavedAt,
            LocalDateTime firstPossibleUnlockAt,
            LocalDateTime solenoidPoweredOffAt,
            LocalDateTime cleanerConfirmedClosedAt,
            LocalDateTime executionDeadlineAt,
            int reopenCount,
            int recoveryCount,
            LocalDateTime createdAt,
            LocalDateTime updatedAt,
            LocalDateTime endedAt,
            String endReason,
            String cleanRecordNo) {
    }

    private record Filters(
            String status,
            String deviceCode,
            Integer portNo,
            LocalDateTime createdFrom,
            LocalDateTime createdTo) {

        static Filters validated(
                String requestedStatus,
                String requestedDeviceCode,
                Integer requestedPortNo,
                Instant requestedCreatedFrom,
                Instant requestedCreatedTo) {
            String status = text(
                    requestedStatus, "status", 24);
            if (status != null && !STATUSES.contains(status)) {
                throw validation("status", "清运操作状态不正确");
            }
            if (requestedPortNo != null
                    && (requestedPortNo < 1 || requestedPortNo > 6)) {
                throw validation("portNo", "投口编号必须在 1 到 6 之间");
            }
            LocalDateTime createdFrom = time(requestedCreatedFrom);
            LocalDateTime createdTo = time(requestedCreatedTo);
            if (createdFrom != null
                    && createdTo != null
                    && !createdFrom.isBefore(createdTo)) {
                throw validation(
                        "createdTo", "结束时间必须晚于开始时间");
            }
            return new Filters(
                    status,
                    text(requestedDeviceCode, "deviceCode", 128),
                    requestedPortNo,
                    createdFrom,
                    createdTo);
        }

        void append(
                StringBuilder sql,
                MapSqlParameterSource parameters) {
            if (status != null) {
                sql.append(" AND operation.status = :status");
                parameters.addValue("status", status);
            }
            if (deviceCode != null) {
                sql.append(" AND asset.device_public_code = :deviceCode");
                parameters.addValue("deviceCode", deviceCode);
            }
            if (portNo != null) {
                sql.append(" AND port.port_no = :portNo");
                parameters.addValue("portNo", portNo);
            }
            if (createdFrom != null) {
                sql.append(" AND operation.created_at >= :createdFrom");
                parameters.addValue("createdFrom", createdFrom);
            }
            if (createdTo != null) {
                sql.append(" AND operation.created_at < :createdTo");
                parameters.addValue("createdTo", createdTo);
            }
        }

        Map<String, Object> fingerprintFields(
                AuthorizedDeliveryScope authorized,
                UUID cleanerUserUid,
                int limit) {
            Map<String, Object> values = new LinkedHashMap<>();
            values.put("channel", "WEB");
            values.put("principalUid", authorized.principalUid());
            values.put("tenantCode", authorized.tenantCode());
            values.put("organizationCode",
                    authorized.organizationCode());
            values.put("status", marker(status));
            values.put("cleanerUserUid", marker(cleanerUserUid));
            values.put("deviceCode", marker(deviceCode));
            values.put("portNo", marker(portNo));
            values.put("createdFrom", marker(createdFrom));
            values.put("createdTo", marker(createdTo));
            values.put("limit", limit);
            return values;
        }

        private static Object marker(Object value) {
            return value == null ? "<null>" : value;
        }
    }
}
