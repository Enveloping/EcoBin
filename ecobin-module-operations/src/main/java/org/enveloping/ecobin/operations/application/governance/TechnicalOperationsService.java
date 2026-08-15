package org.enveloping.ecobin.operations.application.governance;

import tools.jackson.databind.ObjectMapper;
import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationBinding;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationIdempotencyPort;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationResult;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.ManagementScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery.Channel;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.AcceptedOperation;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.CursorPage;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.PageData;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.QuarantineView;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.ReliableTaskView;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.ResumeTaskRequest;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.TaskAttemptView;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.VersionedOperationResult;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.VersionedReasonRequest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.Base64;

@Service
public class TechnicalOperationsService {

    private final JdbcTemplate jdbc;
    private final ManagementScopeAuthorizationPort authorization;
    private final AuditPort audit;
    private final ObjectMapper objectMapper;
    private final GlobalOperationIdempotencyPort idempotency;

    public TechnicalOperationsService(
            JdbcTemplate jdbc,
            ManagementScopeAuthorizationPort authorization,
            AuditPort audit,
            ObjectMapper objectMapper,
            GlobalOperationIdempotencyPort idempotency) {
        this.jdbc = jdbc;
        this.authorization = authorization;
        this.audit = audit;
        this.objectMapper = objectMapper;
        this.idempotency = idempotency;
    }

    @Transactional(readOnly = true)
    public PageData<ReliableTaskView> tasks(
            String state,
            String executionLane,
            String taskKind,
            String taskType,
            String targetType,
            String targetKey,
            Instant createdFrom,
            Instant createdTo,
            Integer requestedPage,
            Integer requestedPageSize) {
        platform();
        int page = page(requestedPage);
        int pageSize = pageSize(requestedPageSize);
        StringBuilder where = new StringBuilder(" WHERE 1 = 1");
        var args = new java.util.ArrayList<Object>();
        append(where, args, "task.state", enumValue(state,
                Set.of("PENDING", "DONE", "CANCELLED", "BLOCKED")));
        append(where, args, "task.execution_lane", enumValue(
                executionLane, Set.of("DEVICE", "FUNDS")));
        append(where, args, "task.task_category", enumValue(
                taskKind, Set.of("BUSINESS_INTENT", "INBOX_PROCESSING",
                        "TIMER", "RECONCILIATION")));
        append(where, args, "task.task_type", text(taskType, 64));
        append(where, args, "task.target_type", text(targetType, 64));
        append(where, args, "task.target_stable_key", text(targetKey, 160));
        appendTime(where, args, "task.created_at", ">=", createdFrom);
        appendTime(where, args, "task.created_at", "<", createdTo);
        Long total = jdbc.queryForObject(
                "SELECT COUNT(*) FROM ops_reliable_task task" + where,
                Long.class, args.toArray());
        args.add(pageSize);
        args.add((page - 1) * pageSize);
        List<ReliableTaskView> items = jdbc.query(taskSelect() + where + """
                        ORDER BY task.updated_at DESC, task.id DESC
                        LIMIT ? OFFSET ?
                        """,
                (rs, ignored) -> taskView(rs), args.toArray());
        return new PageData<>(items, page, pageSize,
                total == null ? 0 : total);
    }

    @Transactional(readOnly = true)
    public ReliableTaskView task(UUID taskUid) {
        platform();
        return findTask(taskUid, false);
    }

    @Transactional(readOnly = true)
    public CursorPage<TaskAttemptView> attempts(
            UUID taskUid, String cursor, Integer requestedLimit) {
        platform();
        ReliableTaskView ignored = findTask(taskUid, false);
        int limit = cursorLimit(requestedLimit);
        Long before = attemptCursor(taskUid, cursor);
        List<TaskAttemptView> rows = jdbc.query("""
                        SELECT attempt.attempt_uid, attempt.attempt_no,
                               attempt.action_kind, attempt.technical_result,
                               attempt.claimed_at,
                               attempt.external_call_may_have_started_at,
                               attempt.result_recorded_at, attempt.http_status,
                               attempt.external_api_error_code,
                               attempt.duration_ms,
                               attempt.redacted_diagnostic
                        FROM ops_task_attempt attempt
                        JOIN ops_reliable_task task
                          ON task.id = attempt.task_id
                        WHERE task.task_uid = ?
                          AND (? IS NULL OR attempt.attempt_no < ?)
                        ORDER BY attempt.attempt_no DESC
                        LIMIT ?
                        """,
                (rs, row) -> new TaskAttemptView(
                        UUID.fromString(rs.getString("attempt_uid")),
                        rs.getLong("attempt_no"),
                        rs.getString("action_kind"),
                        rs.getString("technical_result"),
                        instant(rs.getObject("claimed_at", LocalDateTime.class)),
                        instant(rs.getObject(
                                "external_call_may_have_started_at",
                                LocalDateTime.class)),
                        instant(rs.getObject(
                                "result_recorded_at", LocalDateTime.class)),
                        (Integer) rs.getObject("http_status"),
                        rs.getString("external_api_error_code"),
                        (Long) rs.getObject("duration_ms"),
                        rs.getString("redacted_diagnostic")),
                taskUid.toString(), before, before, limit + 1);
        boolean more = rows.size() > limit;
        List<TaskAttemptView> items = more ? rows.subList(0, limit) : rows;
        return new CursorPage<>(items,
                more ? encodeAttemptCursor(
                        taskUid, items.getLast().attemptNo()) : null);
    }

    @Transactional
    public AcceptedOperation resume(
            UUID taskUid,
            UUID operationUid,
            ResumeTaskRequest request) {
        PlatformAccess actor = platform();
        String digest = GovernanceIdempotency.requestDigest(
                taskUid, request.expectedVersion(),
                request.causeFixedConfirmed(), request.reason());
        var claim = idempotency.claim(new GlobalOperationBinding(
                operationUid, "PLATFORM_ADMIN", actor.principalUid(),
                actor.scopeDigest(), "operations.reliable-task.resume",
                "RELIABLE_TASK", taskUid.toString(), digest));
        if (claim.replay()) {
            return accepted(operationUid, taskUid,
                    claim.result().version());
        }
        if (!Boolean.TRUE.equals(request.causeFixedConfirmed())) {
            throw new TargetApiException(
                    422, "OPERATIONS.CAUSE_FIX_NOT_CONFIRMED",
                    "必须确认阻断原因已经排除");
        }
        return withPlatformWrite(actor, platformAdminId -> resumeOwned(
                actor, platformAdminId, taskUid, operationUid,
                request, digest));
    }

    private AcceptedOperation resumeOwned(
            PlatformAccess actor,
            long platformAdminId,
            UUID taskUid,
            UUID operationUid,
            ResumeTaskRequest request,
            String digest) {
        TaskRow task = lockTask(taskUid);
        if (task.version() != request.expectedVersion()) {
            throw conflict("OPERATIONS.TASK_VERSION_CONFLICT",
                    "可靠任务版本已变化");
        }
        if (!"BLOCKED".equals(task.state())) {
            throw conflict("OPERATIONS.TASK_STATE_CONFLICT",
                    "只有已阻断任务可以恢复");
        }
        if (task.leaseToken() != null || !supported(task)) {
            throw new TargetApiException(
                    422, "OPERATIONS.TASK_RESUMPTION_UNSAFE",
                    "当前任务不能安全恢复");
        }
        LocalDateTime now = databaseNow();
        long resultVersion = task.version() + 1;
        String summary = json(Map.of(
                "requestDigest", digest,
                "resultVersion", resultVersion,
                "state", "PENDING"));
        audit.append(new AuditEntry(
                UUID.randomUUID(), UUID.randomUUID(), operationUid,
                AuditScopeKind.PLATFORM, null, null,
                AuditActorKind.PLATFORM_ADMIN,
                platformAdminId, null, null, null,
                actor.displayName(),
                "operations.reliable-task.resume",
                "RELIABLE_TASK", taskUid.toString(),
                "WEB", "SUCCEEDED", actor.sessionUid(),
                request.reason(), summary, instant(now)));
        int updated = jdbc.update("""
                        UPDATE ops_reliable_task
                        SET state = 'PENDING', next_run_at = ?,
                            completed_at = NULL,
                            blocked_reason_code = NULL,
                            blocked_diagnostic = NULL,
                            consecutive_failure_count = 0,
                            wake_version = wake_version + 1,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND state = 'BLOCKED'
                          AND lock_version = ? AND lease_token IS NULL
                        """,
                now, now, task.id(), task.version());
        requireOne(updated);
        idempotency.succeed(operationUid,
                new GlobalOperationResult(
                        taskUid, "PENDING", resultVersion));
        return accepted(operationUid, taskUid, resultVersion);
    }

    @Transactional(readOnly = true)
    public PageData<QuarantineView> quarantines(
            String state,
            String reasonCode,
            String sourceNamespace,
            Instant detectedFrom,
            Instant detectedTo,
            Integer requestedPage,
            Integer requestedPageSize) {
        platform();
        int page = page(requestedPage);
        int pageSize = pageSize(requestedPageSize);
        StringBuilder where = new StringBuilder(" WHERE 1 = 1");
        var args = new java.util.ArrayList<Object>();
        append(where, args, "quarantine.status", enumValue(
                state, Set.of("OPEN", "ACKNOWLEDGED")));
        append(where, args, "quarantine.reason_code", text(reasonCode, 40));
        append(where, args, "quarantine.source_namespace",
                text(sourceNamespace, 64));
        appendTime(where, args, "quarantine.first_seen_at", ">=", detectedFrom);
        appendTime(where, args, "quarantine.first_seen_at", "<", detectedTo);
        Long total = jdbc.queryForObject(
                "SELECT COUNT(*) FROM ops_message_quarantine quarantine" + where,
                Long.class, args.toArray());
        args.add(pageSize);
        args.add((page - 1) * pageSize);
        List<QuarantineView> items = jdbc.query(
                quarantineSelect() + where + """
                        ORDER BY quarantine.first_seen_at DESC,
                                 quarantine.id DESC
                        LIMIT ? OFFSET ?
                        """, (rs, ignored) -> quarantineView(rs),
                args.toArray());
        return new PageData<>(items, page, pageSize,
                total == null ? 0 : total);
    }

    @Transactional(readOnly = true)
    public QuarantineView quarantine(UUID quarantineUid) {
        platform();
        return findQuarantine(quarantineUid, false);
    }

    @Transactional
    public VersionedOperationResult acknowledgeQuarantine(
            UUID quarantineUid,
            UUID operationUid,
            VersionedReasonRequest request) {
        PlatformAccess actor = platform();
        String digest = GovernanceIdempotency.requestDigest(
                quarantineUid, request.expectedVersion(), request.reason());
        var claim = idempotency.claim(new GlobalOperationBinding(
                operationUid, "PLATFORM_ADMIN", actor.principalUid(),
                actor.scopeDigest(), "operations.quarantine.acknowledge",
                "MESSAGE_QUARANTINE", quarantineUid.toString(), digest));
        if (claim.replay()) {
            return new VersionedOperationResult(
                    operationUid, quarantineUid, "ACKNOWLEDGED",
                    claim.result().version());
        }
        return withPlatformWrite(actor, platformAdminId ->
                acknowledgeQuarantineOwned(
                        actor, platformAdminId, quarantineUid,
                        operationUid, request, digest));
    }

    private VersionedOperationResult acknowledgeQuarantineOwned(
            PlatformAccess actor,
            long platformAdminId,
            UUID quarantineUid,
            UUID operationUid,
            VersionedReasonRequest request,
            String digest) {
        QuarantineRow row = lockQuarantine(quarantineUid);
        if (row.version() != request.expectedVersion()) {
            throw conflict("OPERATIONS.QUARANTINE_VERSION_CONFLICT",
                    "隔离消息版本已变化");
        }
        if (!"OPEN".equals(row.state())) {
            throw conflict("OPERATIONS.QUARANTINE_ALREADY_ACKNOWLEDGED",
                    "隔离消息已经确认");
        }
        LocalDateTime now = databaseNow();
        long resultVersion = row.version() + 1;
        String summary = json(Map.of(
                "requestDigest", digest,
                "resultVersion", resultVersion,
                "state", "ACKNOWLEDGED"));
        long auditId = audit.append(new AuditEntry(
                UUID.randomUUID(), UUID.randomUUID(), operationUid,
                AuditScopeKind.PLATFORM, null, null,
                AuditActorKind.PLATFORM_ADMIN,
                platformAdminId, null, null, null,
                actor.displayName(),
                "operations.quarantine.acknowledge",
                "MESSAGE_QUARANTINE", quarantineUid.toString(),
                "WEB", "SUCCEEDED", actor.sessionUid(),
                request.reason(), summary, instant(now)));
        int updated = jdbc.update("""
                        UPDATE ops_message_quarantine
                        SET status = 'ACKNOWLEDGED',
                            acknowledged_audit_id = ?, acknowledged_at = ?,
                            lock_version = lock_version + 1, updated_at = ?
                        WHERE id = ? AND status = 'OPEN'
                          AND lock_version = ?
                        """,
                auditId, now, now, row.id(), row.version());
        requireOne(updated);
        idempotency.succeed(operationUid,
                new GlobalOperationResult(
                        quarantineUid, "ACKNOWLEDGED", resultVersion));
        return new VersionedOperationResult(
                operationUid, quarantineUid, "ACKNOWLEDGED", resultVersion);
    }

    private PlatformAccess platform() {
        var authorized = authorization.authorize(
                new ManagementScopeAuthorizationQuery(
                        Channel.WEB, true, null, null,
                        "operations.platform"));
        if (!authorized.platformActor()
                || authorized.tenantCode() != null
                || !authorized.organizations().isEmpty()) {
            throw new IllegalStateException(
                    "platform technical scope is invalid");
        }
        return new PlatformAccess(
                authorized.principalUid(),
                GovernanceIdempotency.scopeDigest(authorized),
                authorized.actorDisplayName(),
                authorized.sessionUid(),
                authorized.persistenceRef());
    }

    private static <T> T withPlatformWrite(
            PlatformAccess actor,
            java.util.function.LongFunction<T> function) {
        return actor.persistenceRef().withScopeOnce(
                ManagementScopePersistenceRef.Purpose
                        .OPERATIONS_TECHNICAL_WRITE,
                (tenantId, organizations, platformId, staffId) -> {
                    if (platformId == null || tenantId != null
                            || !organizations.isEmpty()) {
                        throw new IllegalStateException(
                                "platform technical write scope is invalid");
                    }
                    return function.apply(platformId);
                });
    }

    private ReliableTaskView findTask(UUID uid, boolean lock) {
        if (uid == null) {
            throw notFound();
        }
        return jdbc.query(taskSelect()
                        + " WHERE task.task_uid = ?"
                        + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> taskView(rs), uid.toString()).stream()
                .findFirst().orElseThrow(
                        TechnicalOperationsService::notFound);
    }

    private TaskRow lockTask(UUID uid) {
        return jdbc.query("""
                        SELECT id, task_uid, task_type, execution_lane,
                               state, lease_token, lock_version
                        FROM ops_reliable_task
                        WHERE task_uid = ? FOR UPDATE
                        """,
                (rs, ignored) -> new TaskRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("task_uid")),
                        rs.getString("task_type"),
                        rs.getString("execution_lane"),
                        rs.getString("state"),
                        rs.getString("lease_token"),
                        rs.getLong("lock_version")),
                uid.toString()).stream().findFirst().orElseThrow(
                TechnicalOperationsService::notFound);
    }

    private QuarantineView findQuarantine(UUID uid, boolean lock) {
        if (uid == null) {
            throw notFound();
        }
        return jdbc.query(quarantineSelect()
                        + " WHERE quarantine.quarantine_uid = ?"
                        + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> quarantineView(rs), uid.toString())
                .stream().findFirst().orElseThrow(
                        TechnicalOperationsService::notFound);
    }

    private QuarantineRow lockQuarantine(UUID uid) {
        return jdbc.query("""
                        SELECT id, status, lock_version
                        FROM ops_message_quarantine
                        WHERE quarantine_uid = ? FOR UPDATE
                        """,
                (rs, ignored) -> new QuarantineRow(
                        rs.getLong("id"), rs.getString("status"),
                        rs.getLong("lock_version")),
                uid.toString()).stream().findFirst().orElseThrow(
                TechnicalOperationsService::notFound);
    }

    private static String taskSelect() {
        return """
                SELECT task.task_uid, task.task_type, task.task_category,
                       task.execution_lane, task.state, task.lock_version,
                       task.scope_kind, task.target_type,
                       task.target_stable_key, task.next_run_at,
                       task.lease_token, task.lease_until,
                       task.max_auto_attempts, task.attempt_sequence,
                       task.consecutive_failure_count, task.wake_version,
                       task.handled_wake_version, task.blocked_reason_code,
                       task.blocked_diagnostic, task.correlation_uid,
                       task.causation_uid, task.created_at, task.updated_at
                FROM ops_reliable_task task
                """;
    }

    private static ReliableTaskView taskView(ResultSet rs)
            throws SQLException {
        String state = rs.getString("state");
        String taskType = rs.getString("task_type");
        String executionLane = rs.getString("execution_lane");
        return new ReliableTaskView(
                UUID.fromString(rs.getString("task_uid")),
                taskType,
                rs.getString("task_category"),
                executionLane,
                state,
                rs.getLong("lock_version"),
                rs.getString("scope_kind"),
                rs.getString("target_type"),
                rs.getString("target_stable_key"),
                instant(rs.getObject("next_run_at", LocalDateTime.class)),
                rs.getString("lease_token") != null,
                instant(rs.getObject("lease_until", LocalDateTime.class)),
                rs.getInt("max_auto_attempts"),
                rs.getLong("attempt_sequence"),
                rs.getInt("consecutive_failure_count"),
                rs.getLong("wake_version"),
                rs.getLong("handled_wake_version"),
                rs.getString("blocked_reason_code"),
                rs.getString("blocked_diagnostic"),
                uuid(rs.getString("correlation_uid")),
                uuid(rs.getString("causation_uid")),
                instant(rs.getObject("created_at", LocalDateTime.class)),
                instant(rs.getObject("updated_at", LocalDateTime.class)),
                "BLOCKED".equals(state)
                        && rs.getString("lease_token") == null
                        && ReliableTaskResumptionPolicy.supported(
                                executionLane, taskType)
                        ? List.of("RESUME") : List.of());
    }

    private static String quarantineSelect() {
        return """
                SELECT quarantine.quarantine_uid, quarantine.status,
                       quarantine.lock_version, quarantine.scope_kind,
                       quarantine.reason_code, quarantine.source_namespace,
                       LOWER(SHA2(COALESCE(
                           quarantine.source_principal_key, ''), 256))
                           AS source_principal_summary,
                       LOWER(SHA2(COALESCE(
                           quarantine.external_message_id, ''), 256))
                           AS external_message_summary,
                       LOWER(HEX(quarantine.raw_transport_sha256))
                           AS raw_transport_sha256,
                       LOWER(HEX(quarantine.normalized_content_sha256))
                           AS normalized_content_sha256,
                       quarantine.redacted_diagnostic_payload,
                       quarantine.first_seen_at, quarantine.last_seen_at,
                       quarantine.discovery_count,
                       quarantine.acknowledged_at
                FROM ops_message_quarantine quarantine
                """;
    }

    private static QuarantineView quarantineView(ResultSet rs)
            throws SQLException {
        return new QuarantineView(
                UUID.fromString(rs.getString("quarantine_uid")),
                rs.getString("status"),
                rs.getLong("lock_version"),
                rs.getString("scope_kind"),
                rs.getString("reason_code"),
                rs.getString("source_namespace"),
                rs.getString("source_principal_summary"),
                rs.getString("external_message_summary"),
                rs.getString("raw_transport_sha256"),
                rs.getString("normalized_content_sha256"),
                rs.getString("redacted_diagnostic_payload"),
                instant(rs.getObject("first_seen_at", LocalDateTime.class)),
                instant(rs.getObject("last_seen_at", LocalDateTime.class)),
                rs.getLong("discovery_count"),
                instant(rs.getObject("acknowledged_at", LocalDateTime.class)));
    }

    private static boolean supported(TaskRow task) {
        return ReliableTaskResumptionPolicy.supported(
                task.lane(), task.type());
    }

    private AcceptedOperation accepted(
            UUID operationUid, UUID taskUid, long version) {
        return new AcceptedOperation(
                operationUid, taskUid, taskUid, "PENDING", version,
                "/api/v1/web/platform/operations/reliable-tasks/" + taskUid,
                2_000);
    }

    private String digest(Map<String, ?> value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(objectMapper.writeValueAsBytes(value)));
        } catch (Exception exception) {
            throw new IllegalStateException("request digest failed", exception);
        }
    }

    private String json(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException("safe JSON failed", exception);
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> json(String value) {
        try {
            return objectMapper.readValue(value, Map.class);
        } catch (Exception exception) {
            throw new IllegalStateException("stored audit JSON invalid", exception);
        }
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static void append(
            StringBuilder where,
            List<Object> args,
            String column,
            String value) {
        if (value != null) {
            where.append(" AND ").append(column).append(" = ?");
            args.add(value);
        }
    }

    private static void appendTime(
            StringBuilder where,
            List<Object> args,
            String column,
            String operator,
            Instant value) {
        if (value != null) {
            where.append(" AND ").append(column).append(' ')
                    .append(operator).append(" ?");
            args.add(LocalDateTime.ofInstant(value, ZoneOffset.UTC));
        }
    }

    private static String text(String value, int max) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String result = value.trim();
        if (result.length() > max) {
            throw validation();
        }
        return result;
    }

    private static String enumValue(String value, Set<String> allowed) {
        String result = text(value, 64);
        if (result != null && !allowed.contains(result)) {
            throw validation();
        }
        return result;
    }

    private static int page(Integer value) {
        int result = value == null ? 1 : value;
        if (result < 1) {
            throw validation();
        }
        return result;
    }

    private static int pageSize(Integer value) {
        int result = value == null ? 20 : value;
        if (result < 1 || result > 100) {
            throw validation();
        }
        return result;
    }

    private static int cursorLimit(Integer value) {
        return pageSize(value);
    }

    private static Long attemptCursor(UUID taskUid, String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            String decoded = new String(
                    Base64.getUrlDecoder().decode(value),
                    StandardCharsets.UTF_8);
            String[] parts = decoded.split("\\|", -1);
            if (parts.length != 2
                    || !taskUid.toString().equals(parts[0])) {
                throw new IllegalArgumentException();
            }
            long result = Long.parseLong(parts[1]);
            if (result <= 0) {
                throw new NumberFormatException();
            }
            return result;
        } catch (RuntimeException exception) {
            throw new TargetApiException(
                    400, "COMMON.INVALID_CURSOR", "分页游标无效或已过期");
        }
    }

    private static String encodeAttemptCursor(UUID taskUid, long attemptNo) {
        String raw = taskUid + "|" + attemptNo;
        return Base64.getUrlEncoder().withoutPadding().encodeToString(
                raw.getBytes(StandardCharsets.UTF_8));
    }

    private static UUID uuid(String value) {
        return value == null ? null : UUID.fromString(value);
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static void requireOne(int count) {
        if (count != 1) {
            throw new IllegalStateException(
                    "operations mutation updated " + count + " rows");
        }
    }

    private static TargetApiException validation() {
        return new TargetApiException(
                400, "COMMON.VALIDATION_FAILED", "查询参数无效");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "RESOURCE.NOT_FOUND", "资源不存在");
    }

    private static TargetApiException conflict(String code, String message) {
        return new TargetApiException(409, code, message);
    }

    private record PlatformAccess(
            UUID principalUid,
            String scopeDigest,
            String displayName,
            UUID sessionUid,
            ManagementScopePersistenceRef persistenceRef) { }
    private record TaskRow(
            long id,
            UUID uid,
            String type,
            String lane,
            String state,
            String leaseToken,
            long version) { }
    private record QuarantineRow(long id, String state, long version) { }
}
