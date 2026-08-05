package org.enveloping.ecobin.operations.application.governance;

import tools.jackson.databind.ObjectMapper;
import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.ManagementScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.port.GovernanceIdentityQueryPort;
import org.enveloping.ecobin.identity.api.persistence.GovernanceIdentityFilterRef;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;
import org.enveloping.ecobin.identity.api.result.GovernanceIdentityFacts;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery.Channel;
import org.enveloping.ecobin.identity.api.result.AuthorizedManagementScope;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.AlertView;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.AuditLogView;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.CursorPage;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.PageData;
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
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Service
public class GovernanceQueryService {

    private final JdbcTemplate jdbc;
    private final ManagementScopeAuthorizationPort authorization;
    private final AuditPort audit;
    private final ObjectMapper objectMapper;
    private final GovernanceIdempotencyService idempotency;
    private final GovernanceIdentityQueryPort identity;

    public GovernanceQueryService(
            JdbcTemplate jdbc,
            ManagementScopeAuthorizationPort authorization,
            AuditPort audit,
            ObjectMapper objectMapper,
            GovernanceIdempotencyService idempotency,
            GovernanceIdentityQueryPort identity) {
        this.jdbc = jdbc;
        this.authorization = authorization;
        this.audit = audit;
        this.objectMapper = objectMapper;
        this.idempotency = idempotency;
        this.identity = identity;
    }

    @Transactional(readOnly = true)
    public CursorPage<AuditLogView> auditLogs(
            boolean platform,
            String scopeKind,
            String organizationCode,
            String actorKind,
            UUID actorUid,
            String actionCode,
            String result,
            String targetType,
            String targetKey,
            UUID requestUid,
            UUID operationUid,
            Instant occurredFrom,
            Instant occurredTo,
            String cursor,
            Integer requestedLimit) {
        Access access = access(
                platform, Channel.WEB, organizationCode, "audit.read");
        int limit = limit(requestedLimit);
        String cursorScope = auditCursorScope(
                access, scopeKind, organizationCode, actorKind, actorUid,
                actionCode, result, targetType, targetKey, requestUid,
                operationUid, occurredFrom, occurredTo);
        Cursor anchor = cursor(cursorScope, cursor);
        GovernanceIdentityFilterRef identityFilter = identity.prepareFilter(
                organizationCode, actorUid);
        return withScope(
                access,
                ManagementScopePersistenceRef.Purpose
                        .OPERATIONS_GOVERNANCE_QUERY,
                scope -> auditLogsOwned(
                        scope, identityFilter, limit, cursorScope, anchor,
                        scopeKind, actorKind, actionCode, result,
                        targetType, targetKey, requestUid, operationUid,
                        occurredFrom, occurredTo));
    }

    private CursorPage<AuditLogView> auditLogsOwned(
            ScopeAccess access,
            GovernanceIdentityFilterRef identityFilter,
            int limit,
            String cursorScope,
            Cursor anchor,
            String scopeKind,
            String actorKind,
            String actionCode,
            String result,
            String targetType,
            String targetKey,
            UUID requestUid,
            UUID operationUid,
            Instant occurredFrom,
            Instant occurredTo) {
        StringBuilder sql = new StringBuilder(auditSql());
        List<Object> args = new ArrayList<>();
        appendAccess(sql, args, access, "audit");
        appendIdentityFilters(sql, args, identityFilter, "audit");
        append(sql, args, "audit.scope_kind", enumValue(scopeKind,
                Set.of("PLATFORM", "TENANT", "ORGANIZATION", "UNRESOLVED")));
        append(sql, args, "audit.actor_kind", text(actorKind, 24));
        append(sql, args, "audit.action_code", text(actionCode, 100));
        append(sql, args, "audit.result", enumValue(result,
                Set.of("SUCCEEDED", "DENIED", "FAILED")));
        append(sql, args, "audit.target_type", text(targetType, 64));
        append(sql, args, "audit.target_stable_key", text(targetKey, 160));
        appendUuid(sql, args, "audit.request_uid", requestUid);
        appendUuid(sql, args, "audit.operation_uid", operationUid);
        appendTime(sql, args, "audit.occurred_at", ">=", occurredFrom);
        appendTime(sql, args, "audit.occurred_at", "<", occurredTo);
        if (anchor != null) {
            sql.append(" AND (audit.occurred_at < ?"
                    + " OR (audit.occurred_at = ? AND audit.id < ?))");
            args.add(anchor.time());
            args.add(anchor.time());
            args.add(anchor.id());
        }
        sql.append(" ORDER BY audit.occurred_at DESC, audit.id DESC LIMIT ?");
        args.add(limit + 1);
        List<AuditRow> rows = jdbc.query(sql.toString(),
                (rs, ignored) -> auditRow(rs), args.toArray());
        boolean more = rows.size() > limit;
        List<AuditRow> visible = more ? rows.subList(0, limit) : rows;
        GovernanceIdentityFacts facts = auditIdentity(visible);
        return new CursorPage<>(
                visible.stream().map(row -> auditView(row, facts)).toList(),
                more ? encode(cursorScope, visible.getLast().time(),
                        visible.getLast().id()) : null);
    }

    @Transactional(readOnly = true)
    public AuditLogView auditLog(boolean platform, UUID auditUid) {
        Access access = access(platform, Channel.WEB, null, "audit.read");
        return withScope(
                access,
                ManagementScopePersistenceRef.Purpose
                        .OPERATIONS_GOVERNANCE_QUERY,
                scope -> auditLogOwned(scope, auditUid));
    }

    private AuditLogView auditLogOwned(
            ScopeAccess access, UUID auditUid) {
        StringBuilder sql = new StringBuilder(auditSql());
        List<Object> args = new ArrayList<>();
        appendAccess(sql, args, access, "audit");
        sql.append(" AND audit.audit_uid = ?");
        args.add(auditUid.toString());
        AuditRow row = jdbc.query(sql.toString(),
                (rs, ignored) -> auditRow(rs), args.toArray())
                .stream().findFirst().orElseThrow(
                        GovernanceQueryService::notFound);
        return auditView(row, auditIdentity(List.of(row)));
    }

    @Transactional(readOnly = true)
    public PageData<AlertView> alerts(
            boolean platform,
            boolean miniapp,
            String state,
            String acknowledgementState,
            String severity,
            String category,
            String alertCode,
            String organizationCode,
            Instant detectedFrom,
            Instant detectedTo,
            Integer requestedPage,
            Integer requestedPageSize) {
        Access access = access(
                platform,
                miniapp ? Channel.MINIAPP_STAFF : Channel.WEB,
                miniapp ? null : organizationCode,
                "alert.read");
        int page = page(requestedPage);
        int pageSize = pageSize(requestedPageSize);
        GovernanceIdentityFilterRef identityFilter = identity.prepareFilter(
                organizationCode, null);
        return withScope(
                access,
                ManagementScopePersistenceRef.Purpose
                        .OPERATIONS_GOVERNANCE_QUERY,
                scope -> alertsOwned(
                        scope, identityFilter, platform, miniapp,
                        state, acknowledgementState, severity, category,
                        alertCode, detectedFrom, detectedTo, page, pageSize));
    }

    private PageData<AlertView> alertsOwned(
            ScopeAccess access,
            GovernanceIdentityFilterRef identityFilter,
            boolean platform,
            boolean miniapp,
            String state,
            String acknowledgementState,
            String severity,
            String category,
            String alertCode,
            Instant detectedFrom,
            Instant detectedTo,
            int page,
            int pageSize) {
        StringBuilder where = new StringBuilder();
        List<Object> args = new ArrayList<>();
        appendAccess(where, args, access, "alert");
        appendIdentityFilters(where, args, identityFilter, "alert");
        append(where, args, "alert.status", enumValue(state,
                Set.of("OPEN", "RESOLVED")));
        String ack = enumValue(acknowledgementState,
                Set.of("UNACKNOWLEDGED", "ACKNOWLEDGED"));
        if (ack != null) {
            where.append(" AND alert.acknowledged_at IS ")
                    .append("ACKNOWLEDGED".equals(ack)
                            ? "NOT NULL" : "NULL");
        }
        append(where, args, "alert.current_severity", enumValue(
                severity, Set.of("INFO", "WARNING", "CRITICAL")));
        append(where, args, "alert.category", text(category, 32));
        append(where, args, "alert.alert_code", text(alertCode, 64));
        appendTime(where, args, "alert.first_seen_at", ">=", detectedFrom);
        appendTime(where, args, "alert.first_seen_at", "<", detectedTo);
        Long total = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_alert alert
                        """ + where,
                Long.class, args.toArray());
        args.add(pageSize);
        args.add((page - 1) * pageSize);
        List<AlertRowView> rows = jdbc.query(alertSql() + where + """
                        ORDER BY (alert.status = 'OPEN') DESC,
                          FIELD(alert.current_severity,
                                'CRITICAL', 'WARNING', 'INFO'),
                          alert.last_seen_at DESC, alert.id DESC
                        LIMIT ? OFFSET ?
                        """,
                (rs, ignored) -> alertRowView(rs),
                args.toArray());
        GovernanceIdentityFacts facts = alertIdentity(rows);
        List<AlertView> items = rows.stream()
                .map(row -> alertView(row, facts, miniapp, platform))
                .toList();
        return new PageData<>(items, page, pageSize,
                total == null ? 0 : total);
    }

    @Transactional(readOnly = true)
    public AlertView alert(
            boolean platform, boolean miniapp, UUID alertUid) {
        Access access = access(
                platform,
                miniapp ? Channel.MINIAPP_STAFF : Channel.WEB,
                null,
                "alert.read");
        return withScope(
                access,
                ManagementScopePersistenceRef.Purpose
                        .OPERATIONS_GOVERNANCE_QUERY,
                scope -> alertOwned(scope, platform, miniapp, alertUid));
    }

    private AlertView alertOwned(
            ScopeAccess access,
            boolean platform,
            boolean miniapp,
            UUID alertUid) {
        StringBuilder sql = new StringBuilder(alertSql());
        List<Object> args = new ArrayList<>();
        appendAccess(sql, args, access, "alert");
        sql.append(" AND alert.alert_uid = ?");
        args.add(alertUid.toString());
        AlertRowView row = jdbc.query(sql.toString(),
                (rs, ignored) -> alertRowView(rs), args.toArray())
                .stream().findFirst().orElseThrow(
                        GovernanceQueryService::notFound);
        return alertView(row, alertIdentity(List.of(row)), miniapp, platform);
    }

    @Transactional
    public VersionedOperationResult acknowledgeAlert(
            boolean platform,
            UUID alertUid,
            UUID operationUid,
            VersionedReasonRequest request) {
        Access access = access(
                platform, Channel.WEB, null, "alert.acknowledge");
        String digest = GovernanceIdempotency.requestDigest(
                alertUid, request.expectedVersion(), request.reason());
        String actorKind = access.platform()
                ? "PLATFORM_ADMIN" : "STAFF_ACCOUNT";
        var claim = idempotency.claim(new GovernanceIdempotencyService.Request(
                operationUid, actorKind, access.principalUid(),
                access.scopeDigest(), "operations.alert.acknowledge",
                "ALERT", alertUid.toString(), digest));
        if (claim.replay()) {
            return new VersionedOperationResult(
                    operationUid, alertUid, "ACKNOWLEDGED",
                    claim.result().version());
        }
        return withScope(
                access,
                ManagementScopePersistenceRef.Purpose
                        .OPERATIONS_GOVERNANCE_WRITE,
                scope -> acknowledgeAlertOwned(
                        access, scope, alertUid, operationUid,
                        request, digest));
    }

    private VersionedOperationResult acknowledgeAlertOwned(
            Access actor,
            ScopeAccess access,
            UUID alertUid,
            UUID operationUid,
            VersionedReasonRequest request,
            String digest) {
        AlertRow row = lockAlert(access, alertUid);
        if (row.version() != request.expectedVersion()) {
            throw conflict("ALERT.VERSION_CONFLICT", "告警版本已变化");
        }
        if (!"OPEN".equals(row.state())) {
            throw conflict("ALERT.STATE_CONFLICT", "已解决告警不能确认");
        }
        if (row.acknowledged()) {
            throw conflict("ALERT.ALREADY_ACKNOWLEDGED", "告警已经确认");
        }
        LocalDateTime now = databaseNow();
        long resultVersion = row.version() + 1;
        String summary = json(Map.of(
                "requestDigest", digest,
                "resultVersion", resultVersion,
                "state", "ACKNOWLEDGED"));
        AuditScopeKind auditScope = AuditScopeKind.valueOf(row.scopeKind());
        long auditId = audit.append(new AuditEntry(
                UUID.randomUUID(), UUID.randomUUID(), operationUid,
                auditScope, row.tenantId(), row.organizationId(),
                access.platformId() == null
                        ? AuditActorKind.STAFF_ACCOUNT
                        : AuditActorKind.PLATFORM_ADMIN,
                access.platformId(), access.staffId(), null, null,
                actor.displayName(),
                "operations.alert.acknowledge",
                "ALERT", alertUid.toString(), "WEB", "SUCCEEDED",
                actor.sessionUid(), request.reason(), summary, instant(now)));
        int updated = jdbc.update("""
                        UPDATE ops_alert
                        SET acknowledged_at = ?, acknowledged_audit_id = ?,
                            lock_version = lock_version + 1, updated_at = ?
                        WHERE id = ? AND status = 'OPEN'
                          AND acknowledged_at IS NULL AND lock_version = ?
                        """,
                now, auditId, now, row.id(), row.version());
        if (updated != 1) {
            throw new IllegalStateException(
                    "alert acknowledgement lost its locked row");
        }
        idempotency.succeed(operationUid,
                new GovernanceIdempotencyService.Result(
                        alertUid, "ACKNOWLEDGED", resultVersion));
        return new VersionedOperationResult(
                operationUid, alertUid, "ACKNOWLEDGED", resultVersion);
    }

    private Access access(
            boolean platform,
            Channel channel,
            String organizationCode,
            String capability) {
        AuthorizedManagementScope result = authorization.authorize(
                new ManagementScopeAuthorizationQuery(
                        channel, platform, null,
                        platform ? null : organizationCode,
                        capability));
        return new Access(
                result.platformActor(),
                result.tenantCode(),
                result.organizations().stream()
                        .map(AuthorizedManagementScope.Organization::code)
                        .toList(),
                result.tenantWide(),
                result.principalUid(),
                GovernanceIdempotency.scopeDigest(result),
                result.actorDisplayName(),
                result.sessionUid(),
                result.persistenceRef());
    }

    private AlertRow lockAlert(ScopeAccess access, UUID alertUid) {
        StringBuilder sql = new StringBuilder("""
                SELECT alert.id, alert.scope_kind, alert.tenant_id,
                       alert.organization_id, alert.status,
                       alert.acknowledged_at, alert.lock_version
                FROM ops_alert alert
                WHERE 1 = 1
                """);
        List<Object> args = new ArrayList<>();
        appendAccessTail(sql, args, access, "alert");
        sql.append(" AND alert.alert_uid = ? FOR UPDATE");
        args.add(alertUid.toString());
        return jdbc.query(sql.toString(),
                (rs, ignored) -> new AlertRow(
                        rs.getLong("id"),
                        rs.getString("scope_kind"),
                        (Long) rs.getObject("tenant_id"),
                        (Long) rs.getObject("organization_id"),
                        rs.getString("status"),
                        rs.getObject("acknowledged_at") != null,
                        rs.getLong("lock_version")),
                args.toArray()).stream().findFirst().orElseThrow(
                GovernanceQueryService::notFound);
    }

    private void appendAccess(
            StringBuilder sql,
            List<Object> args,
            ScopeAccess access,
            String alias) {
        sql.append(" WHERE 1 = 1");
        appendAccessTail(sql, args, access, alias);
    }

    private <T> T withScope(
            Access access,
            ManagementScopePersistenceRef.Purpose purpose,
            java.util.function.Function<ScopeAccess, T> function) {
        return access.persistenceRef().withScopeOnce(
                purpose,
                (tenantId, organizations, platformId, staffId) ->
                        function.apply(new ScopeAccess(
                                platformId != null,
                                tenantId,
                                organizations.stream()
                                        .map(ManagementScopePersistenceRef
                                                .OrganizationKey::value)
                                        .toList(),
                                access.tenantWide(),
                                platformId,
                                staffId)));
    }

    private static void appendIdentityFilters(
            StringBuilder sql,
            List<Object> args,
            GovernanceIdentityFilterRef filter,
            String alias) {
        filter.consumeOnce((organizationRequested, organizationKeys,
                            actorRequested, platformKeys, staffKeys) -> {
            if (organizationRequested) {
                appendKeys(sql, args,
                        alias + ".organization_id", organizationKeys);
            }
            if (actorRequested) {
                if (platformKeys.isEmpty() && staffKeys.isEmpty()) {
                    sql.append(" AND 1 = 0");
                } else {
                    sql.append(" AND (");
                    boolean appended = false;
                    if (!platformKeys.isEmpty()) {
                        appendKeysWithoutAnd(sql, args,
                                alias + ".platform_admin_id", platformKeys);
                        appended = true;
                    }
                    if (!staffKeys.isEmpty()) {
                        if (appended) {
                            sql.append(" OR ");
                        }
                        appendKeysWithoutAnd(sql, args,
                                alias + ".staff_account_id", staffKeys);
                    }
                    sql.append(')');
                }
            }
            return null;
        });
    }

    private static void appendKeys(
            StringBuilder sql,
            List<Object> args,
            String column,
            List<Long> values) {
        if (values.isEmpty()) {
            sql.append(" AND 1 = 0");
            return;
        }
        sql.append(" AND ");
        appendKeysWithoutAnd(sql, args, column, values);
    }

    private static void appendKeysWithoutAnd(
            StringBuilder sql,
            List<Object> args,
            String column,
            List<Long> values) {
        sql.append(column).append(" IN (")
                .append("?,".repeat(values.size()));
        sql.setLength(sql.length() - 1);
        sql.append(')');
        args.addAll(values);
    }

    private void appendAccessTail(
            StringBuilder sql,
            List<Object> args,
            ScopeAccess access,
            String alias) {
        if (access.platform()) {
            return;
        }
        sql.append(" AND ").append(alias).append(".tenant_id = ? AND (");
        args.add(access.tenantId());
        boolean hasClause = false;
        if (access.tenantWide()) {
            sql.append(alias).append(".scope_kind = 'TENANT'");
            hasClause = true;
        }
        if (!access.organizationIds().isEmpty()) {
            if (hasClause) {
                sql.append(" OR ");
            }
            sql.append('(').append(alias)
                    .append(".scope_kind = 'ORGANIZATION' AND ")
                    .append(alias).append(".organization_id IN (")
                    .append("?,".repeat(access.organizationIds().size()));
            sql.setLength(sql.length() - 1);
            sql.append("))");
            args.addAll(access.organizationIds());
            hasClause = true;
        }
        if (!hasClause) {
            sql.append("1 = 0");
        }
        sql.append(')');
    }

    private static String auditSql() {
        return """
                SELECT audit.id, audit.audit_uid, audit.request_uid,
                       audit.operation_uid, audit.scope_kind,
                       audit.tenant_id, audit.organization_id,
                       audit.actor_kind, audit.platform_admin_id,
                       audit.staff_account_id,
                       audit.actor_display_snapshot,
                       audit.action_code, audit.target_type,
                       audit.target_stable_key, audit.entry_channel,
                       audit.result, audit.reason,
                       CAST(audit.safe_change_summary AS CHAR)
                           AS safe_change_summary,
                       audit.correlation_uid, audit.causation_uid,
                       audit.occurred_at
                FROM ops_audit_log audit
                """;
    }

    private AuditRow auditRow(ResultSet rs) throws SQLException {
        Instant time = instant(rs.getObject(
                "occurred_at", LocalDateTime.class));
        return new AuditRow(
                rs.getLong("id"),
                time,
                UUID.randomUUID(),
                (Long) rs.getObject("tenant_id"),
                (Long) rs.getObject("organization_id"),
                rs.getString("actor_kind"),
                (Long) rs.getObject("platform_admin_id"),
                (Long) rs.getObject("staff_account_id"),
                UUID.fromString(rs.getString("audit_uid")),
                UUID.fromString(rs.getString("request_uid")),
                uuid(rs.getString("operation_uid")),
                rs.getString("scope_kind"),
                rs.getString("actor_display_snapshot"),
                rs.getString("action_code"),
                rs.getString("target_type"),
                rs.getString("target_stable_key"),
                rs.getString("entry_channel"),
                rs.getString("result"),
                rs.getString("reason"),
                jsonNullable(rs.getString("safe_change_summary")),
                uuid(rs.getString("correlation_uid")),
                uuid(rs.getString("causation_uid")));
    }

    private GovernanceIdentityFacts auditIdentity(List<AuditRow> rows) {
        return identity.resolve(new GovernanceIdentityBatch(
                rows.stream().map(row -> new GovernanceIdentityBatch.Entry(
                        row.identityToken(), row.tenantId(),
                        row.organizationId(), row.actorKind(),
                        row.platformId(), row.staffId())).toList()));
    }

    private static AuditLogView auditView(
            AuditRow row, GovernanceIdentityFacts facts) {
        GovernanceIdentityFacts.Entry identity = facts.entries()
                .get(row.identityToken());
        if (identity == null) {
            throw new IllegalStateException("audit identity facts missing");
        }
        return new AuditLogView(
                row.auditUid(), row.requestUid(), row.operationUid(),
                row.scopeKind(), identity.tenantCode(),
                identity.organizationCode(), row.actorKind(),
                identity.actorUid(), row.actorDisplayName(),
                row.actionCode(), row.targetType(), row.targetKey(),
                row.entryChannel(), row.result(), row.reason(),
                row.safeChangeSummary(), row.correlationUid(),
                row.causationUid(), row.time());
    }

    private static String alertSql() {
        return """
                SELECT alert.id, alert.alert_uid, alert.status,
                       alert.lock_version, alert.category, alert.alert_code,
                       alert.current_severity, alert.highest_severity,
                       alert.scope_kind, alert.tenant_id,
                       alert.organization_id,
                       alert.source_kind, alert.source_type, alert.source_key,
                       alert.first_seen_at, alert.last_seen_at,
                       alert.discovery_count, alert.acknowledged_at,
                       ack.actor_display_snapshot acknowledged_by,
                       alert.resolved_at,
                       CAST(alert.safe_display_parameters AS CHAR)
                           AS safe_display_parameters
                FROM ops_alert alert
                LEFT JOIN ops_audit_log ack
                  ON ack.id = alert.acknowledged_audit_id
                """;
    }

    private AlertRowView alertRowView(ResultSet rs)
            throws SQLException {
        String state = rs.getString("status");
        String sourceType = rs.getString("source_type");
        return new AlertRowView(
                UUID.randomUUID(),
                (Long) rs.getObject("tenant_id"),
                (Long) rs.getObject("organization_id"),
                UUID.fromString(rs.getString("alert_uid")),
                state,
                rs.getLong("lock_version"),
                rs.getString("category"),
                rs.getString("alert_code"),
                rs.getString("current_severity"),
                rs.getString("highest_severity"),
                rs.getString("scope_kind"),
                rs.getString("source_kind"),
                sourceType,
                rs.getString("source_key"),
                instant(rs.getObject("first_seen_at", LocalDateTime.class)),
                instant(rs.getObject("last_seen_at", LocalDateTime.class)),
                rs.getLong("discovery_count"),
                rs.getObject("acknowledged_at") != null,
                rs.getString("acknowledged_by"),
                instant(rs.getObject("acknowledged_at", LocalDateTime.class)),
                instant(rs.getObject("resolved_at", LocalDateTime.class)),
                jsonNullable(rs.getString("safe_display_parameters")));
    }

    private GovernanceIdentityFacts alertIdentity(List<AlertRowView> rows) {
        return identity.resolve(new GovernanceIdentityBatch(
                rows.stream().map(row -> new GovernanceIdentityBatch.Entry(
                        row.identityToken(), row.tenantId(),
                        row.organizationId(), "SYSTEM", null, null))
                        .toList()));
    }

    private static AlertView alertView(
            AlertRowView row,
            GovernanceIdentityFacts facts,
            boolean miniapp,
            boolean platform) {
        GovernanceIdentityFacts.Entry identity = facts.entries()
                .get(row.identityToken());
        if (identity == null) {
            throw new IllegalStateException("alert identity facts missing");
        }
        boolean technicalHidden = !platform
                && "RELIABLE_TASK".equals(row.sourceType());
        String sourceKey = miniapp || technicalHidden
                ? null : row.sourceKey();
        Map<String, Object> parameters = miniapp
                ? miniappParameters(row.displayParameters())
                : technicalHidden ? Map.of() : row.displayParameters();
        return new AlertView(
                row.alertUid(), row.state(), row.version(), row.category(),
                row.alertCode(), row.currentSeverity(),
                row.highestSeverity(), row.scopeKind(),
                identity.tenantCode(), identity.organizationCode(),
                miniapp ? null : row.sourceKind(), row.sourceType(),
                sourceKey, row.firstDetectedAt(), row.lastDetectedAt(),
                row.discoveryCount(), row.acknowledged(),
                miniapp ? null : row.acknowledgedBy(),
                row.acknowledgedAt(), row.resolvedAt(), parameters,
                nextActions(row.state(), miniapp, platform,
                        row.sourceType()));
    }

    private static Map<String, Object> miniappParameters(
            Map<String, Object> parameters) {
        var result = new java.util.LinkedHashMap<String, Object>();
        for (String key : List.of(
                "deploymentCode", "portNo", "withdrawalNo", "rechargeNo")) {
            if (parameters.containsKey(key)) {
                result.put(key, parameters.get(key));
            }
        }
        return Map.copyOf(result);
    }

    private static List<String> nextActions(
            String state,
            boolean miniapp,
            boolean platform,
            String sourceType) {
        if (miniapp) {
            return Set.of("DEVICE_FAULT", "PORT_FULLNESS")
                    .contains(sourceType)
                    ? List.of("VIEW_DEVICE") : List.of();
        }
        if (!platform && "RELIABLE_TASK".equals(sourceType)) {
            return "OPEN".equals(state)
                    ? List.of("ACKNOWLEDGE") : List.of();
        }
        return "OPEN".equals(state)
                ? List.of("ACKNOWLEDGE", "VIEW_SOURCE")
                : List.of("VIEW_SOURCE");
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
            throw new IllegalStateException("stored JSON invalid", exception);
        }
    }

    private Map<String, Object> jsonNullable(String value) {
        return value == null ? Map.of() : json(value);
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static void append(
            StringBuilder sql,
            List<Object> args,
            String column,
            String value) {
        if (value != null) {
            sql.append(" AND ").append(column).append(" = ?");
            args.add(value);
        }
    }

    private static void appendUuid(
            StringBuilder sql,
            List<Object> args,
            String column,
            UUID value) {
        if (value != null) {
            sql.append(" AND ").append(column).append(" = ?");
            args.add(value.toString());
        }
    }

    private static void appendTime(
            StringBuilder sql,
            List<Object> args,
            String column,
            String operator,
            Instant value) {
        if (value != null) {
            sql.append(" AND ").append(column).append(' ')
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

    private static int limit(Integer value) {
        return pageSize(value);
    }

    private static String encode(String cursorScope, Instant time, long id) {
        String value = cursorScope + "|" + time + "|" + id;
        return Base64.getUrlEncoder().withoutPadding().encodeToString(
                value.getBytes(StandardCharsets.UTF_8));
    }

    private static Cursor cursor(String cursorScope, String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            String decoded = new String(Base64.getUrlDecoder().decode(value),
                    StandardCharsets.UTF_8);
            String[] parts = decoded.split("\\|", -1);
            if (parts.length != 3 || !cursorScope.equals(parts[0])) {
                throw new IllegalArgumentException();
            }
            return new Cursor(
                    LocalDateTime.ofInstant(
                            Instant.parse(parts[1]), ZoneOffset.UTC),
                    Long.parseLong(parts[2]));
        } catch (Exception exception) {
            throw new TargetApiException(
                    400, "COMMON.INVALID_CURSOR", "分页游标无效或已过期");
        }
    }

    private String auditCursorScope(Access access, Object... filters) {
        var value = new java.util.LinkedHashMap<String, Object>();
        value.put("platform", access.platform());
        value.put("tenantCode", access.tenantCode());
        value.put("organizationCodes", access.organizationCodes());
        value.put("tenantWide", access.tenantWide());
        value.put("filters", java.util.Arrays.asList(filters));
        return digest(value);
    }

    private static UUID uuid(String value) {
        return value == null ? null : UUID.fromString(value);
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
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

    private record Access(
            boolean platform,
            String tenantCode,
            List<String> organizationCodes,
            boolean tenantWide,
            UUID principalUid,
            String scopeDigest,
            String displayName,
            UUID sessionUid,
            ManagementScopePersistenceRef persistenceRef) { }
    private record ScopeAccess(
            boolean platform,
            Long tenantId,
            List<Long> organizationIds,
            boolean tenantWide,
            Long platformId,
            Long staffId) { }
    private record Cursor(LocalDateTime time, long id) { }
    private record AuditRow(
            long id,
            Instant time,
            UUID identityToken,
            Long tenantId,
            Long organizationId,
            String actorKind,
            Long platformId,
            Long staffId,
            UUID auditUid,
            UUID requestUid,
            UUID operationUid,
            String scopeKind,
            String actorDisplayName,
            String actionCode,
            String targetType,
            String targetKey,
            String entryChannel,
            String result,
            String reason,
            Map<String, Object> safeChangeSummary,
            UUID correlationUid,
            UUID causationUid) { }
    private record AlertRowView(
            UUID identityToken,
            Long tenantId,
            Long organizationId,
            UUID alertUid,
            String state,
            long version,
            String category,
            String alertCode,
            String currentSeverity,
            String highestSeverity,
            String scopeKind,
            String sourceKind,
            String sourceType,
            String sourceKey,
            Instant firstDetectedAt,
            Instant lastDetectedAt,
            long discoveryCount,
            boolean acknowledged,
            String acknowledgedBy,
            Instant acknowledgedAt,
            Instant resolvedAt,
            Map<String, Object> displayParameters) { }
    private record AlertRow(
            long id,
            String scopeKind,
            Long tenantId,
            Long organizationId,
            String state,
            boolean acknowledged,
            long version) { }
}
