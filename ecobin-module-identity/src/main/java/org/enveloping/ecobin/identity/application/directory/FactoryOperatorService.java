package org.enveloping.ecobin.identity.application.directory;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappBindingIntentService;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.PageData;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.CreateFactoryOperatorRequest;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.FactoryBindingIntentCreated;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.FactoryBindingRevocationRequest;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.FactoryOperatorStatusRequest;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.FactoryOperatorView;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.UpdateFactoryOperatorRequest;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

@Service
public class FactoryOperatorService {

    private static final int DEFAULT_PAGE_SIZE = 20;
    private static final int MAX_PAGE_SIZE = 200;

    private final JdbcTemplate jdbc;
    private final AuditPort auditPort;
    private final ObjectMapper objectMapper;
    private final PlatformMiniappBindingIntentService bindingIntents;

    public FactoryOperatorService(
            JdbcTemplate jdbc,
            AuditPort auditPort,
            ObjectMapper objectMapper,
            PlatformMiniappBindingIntentService bindingIntents) {
        this.jdbc = jdbc;
        this.auditPort = auditPort;
        this.objectMapper = objectMapper;
        this.bindingIntents = bindingIntents;
    }

    @Transactional(readOnly = true)
    public PageData<FactoryOperatorView> list(
            int requestedPage,
            int requestedPageSize,
            String requestedStatus,
            String requestedQuery) {
        requirePlatformActor();
        int page = Math.max(1, requestedPage);
        int pageSize = Math.min(
                MAX_PAGE_SIZE,
                requestedPageSize <= 0
                        ? DEFAULT_PAGE_SIZE : requestedPageSize);
        String status = normalizeStatus(requestedStatus);
        String query = normalizeQuery(requestedQuery);
        StringBuilder where = new StringBuilder(" WHERE 1 = 1");
        List<Object> parameters = new ArrayList<>();
        if (status != null) {
            where.append(" AND o.enabled = ?");
            parameters.add("ACTIVE".equals(status) ? 1 : 0);
        }
        if (query != null) {
            where.append("""
                     AND (
                         o.operator_code LIKE ? ESCAPE '!'
                         OR LOWER(o.display_name) LIKE ? ESCAPE '!'
                     )
                    """);
            String pattern = "%" + escapeLike(query) + "%";
            parameters.add(pattern);
            parameters.add(pattern);
        }
        Long total = jdbc.queryForObject(
                "SELECT COUNT(*) FROM iam_factory_operator o" + where,
                Long.class,
                parameters.toArray());
        List<Object> pageParameters = new ArrayList<>(parameters);
        pageParameters.add(pageSize);
        pageParameters.add((page - 1L) * pageSize);
        List<FactoryOperatorView> records = jdbc.query(
                selectSql() + where + """
                         ORDER BY o.operator_code, o.id
                         LIMIT ? OFFSET ?
                        """,
                (rs, ignored) -> view(rs),
                pageParameters.toArray());
        return new PageData<>(
                List.copyOf(records),
                page,
                pageSize,
                total == null ? 0 : total);
    }

    @Transactional(readOnly = true)
    public FactoryOperatorView get(UUID factoryOperatorUid) {
        requirePlatformActor();
        return find(factoryOperatorUid, false);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public FactoryOperatorView create(
            UUID operationUid,
            CreateFactoryOperatorRequest request) {
        requireUuidV4(operationUid);
        TargetWebActor actor = requirePlatformActor();
        String code = normalizeCode(request.operatorCode());
        String displayName = required(request.displayName(), 100, "姓名");
        byte[] fingerprint = fingerprint(
                "CREATE", null, Map.of(
                        "operatorCode", code,
                        "displayName", displayName));
        FactoryOperatorView replay = replay(
                operationUid,
                "identity.factory-operator.create",
                fingerprint);
        if (replay != null) return replay;
        UUID uid = UUID.randomUUID();
        try {
            jdbc.update("""
                            INSERT INTO iam_factory_operator (
                                factory_operator_uid, operator_code,
                                display_name, enabled, auth_version,
                                lock_version, created_by_platform_admin_id,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, 1, 0, 0, ?,
                                      UTC_TIMESTAMP(3), UTC_TIMESTAMP(3))
                            """,
                    uid.toString(),
                    code,
                    displayName,
                    actor.principalId());
        } catch (DataIntegrityViolationException conflict) {
            throw conflict(
                    "IDENTITY.FACTORY_OPERATOR_CODE_ALREADY_USED",
                    "厂家操作员工号已被使用");
        }
        FactoryOperatorView created = find(uid, false);
        appendAudit(actor, operationUid,
                "identity.factory-operator.create",
                created, null, fingerprint);
        return created;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public FactoryOperatorView update(
            UUID operationUid,
            UUID factoryOperatorUid,
            UpdateFactoryOperatorRequest request) {
        requireUuidV4(operationUid);
        TargetWebActor actor = requirePlatformActor();
        String displayName = required(request.displayName(), 100, "姓名");
        byte[] fingerprint = fingerprint(
                "UPDATE", factoryOperatorUid, Map.of(
                        "expectedVersion", request.expectedVersion(),
                        "displayName", displayName));
        FactoryOperatorView replay = replay(
                operationUid,
                "identity.factory-operator.update",
                fingerprint);
        if (replay != null) return replay;
        FactoryOperatorView before = find(factoryOperatorUid, true);
        requireVersion(before, request.expectedVersion());
        int updated = jdbc.update("""
                        UPDATE iam_factory_operator
                        SET display_name = ?, lock_version = lock_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE factory_operator_uid = ? AND lock_version = ?
                        """,
                displayName,
                factoryOperatorUid.toString(),
                request.expectedVersion());
        requireUpdated(updated);
        FactoryOperatorView after = find(factoryOperatorUid, false);
        appendAudit(actor, operationUid,
                "identity.factory-operator.update",
                after, null, fingerprint);
        return after;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public FactoryOperatorView changeStatus(
            UUID operationUid,
            UUID factoryOperatorUid,
            FactoryOperatorStatusRequest request,
            boolean enabled) {
        requireUuidV4(operationUid);
        TargetWebActor actor = requirePlatformActor();
        String reason = required(request.reason(), 500, "原因");
        String action = enabled
                ? "identity.factory-operator.activate"
                : "identity.factory-operator.deactivate";
        byte[] fingerprint = fingerprint(
                enabled ? "ACTIVATE" : "DEACTIVATE",
                factoryOperatorUid,
                Map.of(
                        "expectedVersion", request.expectedVersion(),
                        "reason", reason));
        FactoryOperatorView replay = replay(
                operationUid, action, fingerprint);
        if (replay != null) return replay;
        FactoryOperatorView before = find(factoryOperatorUid, true);
        requireVersion(before, request.expectedVersion());
        if (("ACTIVE".equals(before.status())) == enabled) {
            throw conflict(
                    "IDENTITY.FACTORY_OPERATOR_STATUS_UNCHANGED",
                    enabled ? "厂家操作员已经启用" : "厂家操作员已经停用");
        }
        int updated = jdbc.update("""
                        UPDATE iam_factory_operator
                        SET enabled = ?, auth_version = auth_version + 1,
                            lock_version = lock_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE factory_operator_uid = ? AND lock_version = ?
                        """,
                enabled ? 1 : 0,
                factoryOperatorUid.toString(),
                request.expectedVersion());
        requireUpdated(updated);
        if (!enabled) {
            long id = id(factoryOperatorUid);
            LocalDateTime now = databaseNow();
            jdbc.update("""
                            UPDATE iam_factory_operator_binding_intent
                            SET status = 'CANCELLED'
                            WHERE factory_operator_id = ?
                              AND status = 'PENDING'
                            """,
                    id);
            revokeSessionsAndBinding(id, now, reason);
        }
        FactoryOperatorView after = find(factoryOperatorUid, false);
        appendAudit(actor, operationUid, action,
                after, reason, fingerprint);
        return after;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public FactoryOperatorView revokeBinding(
            UUID operationUid,
            UUID factoryOperatorUid,
            FactoryBindingRevocationRequest request) {
        requireUuidV4(operationUid);
        TargetWebActor actor = requirePlatformActor();
        String reason = required(request.reason(), 500, "解绑原因");
        byte[] fingerprint = fingerprint(
                "REVOKE_BINDING", factoryOperatorUid, Map.of(
                        "expectedVersion", request.expectedVersion(),
                        "reason", reason));
        FactoryOperatorView replay = replay(
                operationUid,
                "identity.factory-operator.binding.revoke",
                fingerprint);
        if (replay != null) return replay;
        FactoryOperatorView before = find(factoryOperatorUid, true);
        requireVersion(before, request.expectedVersion());
        if (!"ACTIVE".equals(before.bindingStatus())) {
            throw conflict(
                    "IDENTITY.FACTORY_OPERATOR_NOT_BOUND",
                    "厂家操作员当前没有有效微信绑定");
        }
        int updated = jdbc.update("""
                        UPDATE iam_factory_operator
                        SET auth_version = auth_version + 1,
                            lock_version = lock_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE factory_operator_uid = ? AND lock_version = ?
                        """,
                factoryOperatorUid.toString(),
                request.expectedVersion());
        requireUpdated(updated);
        revokeSessionsAndBinding(
                id(factoryOperatorUid), databaseNow(), reason);
        FactoryOperatorView after = find(factoryOperatorUid, false);
        appendAudit(actor, operationUid,
                "identity.factory-operator.binding.revoke",
                after, reason, fingerprint);
        return after;
    }

    public FactoryBindingIntentCreated createBindingIntent(
            UUID factoryOperatorUid) {
        requirePlatformActor();
        return bindingIntents.create(factoryOperatorUid);
    }

    private void revokeSessionsAndBinding(
            long factoryOperatorId,
            LocalDateTime now,
            String reason) {
        jdbc.update("""
                        UPDATE iam_factory_operator_miniapp_session
                        SET revoked_at = ?, revocation_reason = ?
                        WHERE factory_operator_id = ?
                          AND revoked_at IS NULL
                        """,
                now, reason, factoryOperatorId);
        jdbc.update("""
                        UPDATE iam_factory_operator_miniapp_binding
                        SET status = 'REVOKED', revoked_at = ?,
                            revocation_reason = ?, lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE factory_operator_id = ? AND status = 'ACTIVE'
                        """,
                now, reason, now, factoryOperatorId);
    }

    private FactoryOperatorView replay(
            UUID operationUid,
            String action,
            byte[] fingerprint) {
        SuccessfulAudit prior = auditPort.findSuccessful(operationUid)
                .orElse(null);
        if (prior == null) return null;
        JsonNode summary = objectMapper.readTree(
                prior.safeChangeSummaryJson());
        String priorFingerprint = summary.path("requestSha256").asText("");
        if (!action.equals(prior.actionCode())
                || !HexFormat.of().formatHex(fingerprint)
                .equals(priorFingerprint)
                || !prior.targetStableKey().startsWith(
                "factory-operator:")) {
            throw conflict(
                    "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                    "相同操作标识已经绑定到不同请求");
        }
        return find(UUID.fromString(prior.targetStableKey().substring(
                "factory-operator:".length())), false);
    }

    private void appendAudit(
            TargetWebActor actor,
            UUID operationUid,
            String action,
            FactoryOperatorView target,
            String reason,
            byte[] fingerprint) {
        TargetWebAuditRequestContext.describe(
                action,
                "factory-operator:" + target.factoryOperatorUid());
        auditPort.append(new AuditEntry(
                UUID.randomUUID(),
                UUID.randomUUID(),
                operationUid,
                AuditScopeKind.PLATFORM,
                null,
                null,
                AuditActorKind.PLATFORM_ADMIN,
                actor.principalId(),
                null,
                null,
                null,
                actor.displayName(),
                action,
                "factory-operator",
                "factory-operator:" + target.factoryOperatorUid(),
                "WEB",
                "SUCCEEDED",
                actor.sessionUid(),
                reason,
                objectMapper.writeValueAsString(Map.of(
                        "requestSha256",
                        HexFormat.of().formatHex(fingerprint),
                        "status", target.status(),
                        "bindingStatus", target.bindingStatus())),
                Instant.now()));
    }

    private FactoryOperatorView find(
            UUID factoryOperatorUid,
            boolean forUpdate) {
        String sql = selectSql() + """
                 WHERE o.factory_operator_uid = ?
                """ + (forUpdate ? " FOR UPDATE" : "");
        return jdbc.query(
                        sql,
                        (rs, ignored) -> view(rs),
                        factoryOperatorUid.toString())
                .stream()
                .findFirst()
                .orElseThrow(() -> new TargetApiException(
                        404,
                        "RESOURCE.NOT_FOUND",
                        "厂家操作员不存在"));
    }

    private long id(UUID factoryOperatorUid) {
        Long id = jdbc.queryForObject("""
                        SELECT id FROM iam_factory_operator
                        WHERE factory_operator_uid = ?
                        """,
                Long.class,
                factoryOperatorUid.toString());
        if (id == null) throw new IllegalStateException("operator id missing");
        return id;
    }

    private static String selectSql() {
        return """
                SELECT o.factory_operator_uid, o.operator_code,
                       o.display_name, o.enabled, o.lock_version,
                       o.auth_version, o.created_at, o.updated_at,
                       b.binding_uid, b.status AS binding_status,
                       b.bound_at
                FROM iam_factory_operator o
                LEFT JOIN iam_factory_operator_miniapp_binding b
                  ON b.factory_operator_id = o.id
                 AND b.status = 'ACTIVE'
                """;
    }

    private static FactoryOperatorView view(ResultSet rs)
            throws SQLException {
        String bindingUid = rs.getString("binding_uid");
        return new FactoryOperatorView(
                UUID.fromString(rs.getString("factory_operator_uid")),
                rs.getString("operator_code"),
                rs.getString("display_name"),
                rs.getBoolean("enabled") ? "ACTIVE" : "DISABLED",
                rs.getLong("lock_version"),
                rs.getLong("auth_version"),
                bindingUid == null ? "UNBOUND" : rs.getString(
                        "binding_status"),
                bindingUid == null ? null : UUID.fromString(bindingUid),
                nullableInstant(rs, "bound_at"),
                instant(rs, "created_at"),
                instant(rs, "updated_at"));
    }

    private byte[] fingerprint(
            String action,
            UUID target,
            Object body) {
        return sha256(objectMapper.writeValueAsBytes(Map.of(
                "schemaVersion", 1,
                "action", action,
                "target", target == null ? "" : target.toString(),
                "body", body)));
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException unavailable) {
            throw new IllegalStateException("SHA-256 is unavailable", unavailable);
        }
    }

    private static TargetWebActor requirePlatformActor() {
        TargetWebActor actor = TargetWebActorContext.required();
        if (!actor.platform()) {
            throw new TargetApiException(
                    403,
                    "AUTH.CAPABILITY_REQUIRED",
                    "仅平台管理员可以管理厂家操作员");
        }
        return actor;
    }

    private static void requireVersion(
            FactoryOperatorView current,
            Long expectedVersion) {
        if (expectedVersion == null || current.version() != expectedVersion) {
            throw conflict(
                    "COMMON.VERSION_CONFLICT",
                    "厂家操作员状态已经变化，请刷新后重试");
        }
    }

    private static void requireUpdated(int rows) {
        if (rows != 1) {
            throw conflict(
                    "COMMON.VERSION_CONFLICT",
                    "厂家操作员状态已经变化，请刷新后重试");
        }
    }

    private static void requireUuidV4(UUID value) {
        if (value == null || value.version() != 4) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_REQUEST",
                    "Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static String normalizeCode(String value) {
        String normalized = required(value, 64, "工号")
                .toUpperCase(Locale.ROOT);
        if (!normalized.matches("^[A-Z0-9][A-Z0-9_-]{1,63}$")) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_REQUEST",
                    "工号只能包含字母、数字、下划线和连字符");
        }
        return normalized;
    }

    private static String normalizeStatus(String value) {
        if (value == null || value.isBlank()) return null;
        String normalized = value.trim().toUpperCase(Locale.ROOT);
        if (!normalized.equals("ACTIVE")
                && !normalized.equals("DISABLED")) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_REQUEST",
                    "状态只能是 ACTIVE 或 DISABLED");
        }
        return normalized;
    }

    private static String normalizeQuery(String value) {
        if (value == null || value.isBlank()) return null;
        String normalized = value.trim().toLowerCase(Locale.ROOT);
        if (normalized.length() > 100) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_REQUEST",
                    "查询内容不能超过 100 个字符");
        }
        return normalized;
    }

    private static String required(
            String value,
            int maxLength,
            String field) {
        String normalized = value == null ? "" : value.trim();
        if (normalized.isEmpty() || normalized.length() > maxLength) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_REQUEST",
                    field + "不能为空且不能超过 " + maxLength + " 个字符");
        }
        return normalized;
    }

    private static String escapeLike(String value) {
        return value.replace("!", "!!")
                .replace("%", "!%")
                .replace("_", "!_");
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static Instant instant(ResultSet rs, String column)
            throws SQLException {
        return rs.getObject(column, LocalDateTime.class)
                .toInstant(ZoneOffset.UTC);
    }

    private static Instant nullableInstant(ResultSet rs, String column)
            throws SQLException {
        LocalDateTime value = rs.getObject(column, LocalDateTime.class);
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static TargetApiException conflict(
            String code,
            String message) {
        return new TargetApiException(409, code, message);
    }
}
