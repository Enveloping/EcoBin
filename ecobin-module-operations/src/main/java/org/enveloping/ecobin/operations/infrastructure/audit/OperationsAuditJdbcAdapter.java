package org.enveloping.ecobin.operations.infrastructure.audit;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Component
public class OperationsAuditJdbcAdapter implements AuditPort {

    private final JdbcTemplate jdbc;

    public OperationsAuditJdbcAdapter(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<SuccessfulAudit> findSuccessful(UUID operationUid) {
        List<SuccessfulAudit> entries = jdbc.query("""
                        SELECT operation_uid, actor_kind, platform_admin_id,
                               staff_account_id, organization_user_id,
                               scope_kind, tenant_id, organization_id,
                               action_code, target_type,
                               target_stable_key, safe_change_summary
                        FROM ops_audit_log
                        WHERE succeeded_operation_uid = ?
                        """,
                (resultSet, rowNumber) -> new SuccessfulAudit(
                        UUID.fromString(resultSet.getString("operation_uid")),
                        AuditActorKind.valueOf(resultSet.getString("actor_kind")),
                        nullableLong(resultSet, "platform_admin_id"),
                        nullableLong(resultSet, "staff_account_id"),
                        nullableLong(resultSet, "organization_user_id"),
                        AuditScopeKind.valueOf(resultSet.getString("scope_kind")),
                        nullableLong(resultSet, "tenant_id"),
                        nullableLong(resultSet, "organization_id"),
                        resultSet.getString("action_code"),
                        resultSet.getString("target_type"),
                        resultSet.getString("target_stable_key"),
                        resultSet.getString("safe_change_summary")),
                operationUid.toString());
        return entries.stream().findFirst();
    }

    @Override
    public void append(AuditEntry entry) {
        jdbc.update("""
                        INSERT INTO ops_audit_log (
                            audit_uid, request_uid, operation_uid, scope_kind,
                            tenant_id, organization_id, actor_kind,
                            platform_admin_id, staff_account_id,
                            organization_user_id, system_actor_code,
                            actor_display_snapshot,
                            action_code, target_type, target_stable_key,
                            entry_channel, result, session_uid, reason,
                            safe_change_summary, occurred_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?
                        )
                        """,
                entry.auditUid().toString(),
                entry.requestUid().toString(),
                entry.operationUid() == null
                        ? null : entry.operationUid().toString(),
                entry.scopeKind().name(),
                entry.tenantId(),
                entry.organizationId(),
                entry.actorKind().name(),
                entry.platformAdminId(),
                entry.staffAccountId(),
                entry.organizationUserId(),
                entry.systemActorCode(),
                entry.actorDisplaySnapshot(),
                entry.actionCode(),
                entry.targetType(),
                entry.targetStableKey(),
                entry.entryChannel(),
                entry.result(),
                entry.sessionUid() == null ? null : entry.sessionUid().toString(),
                entry.reason(),
                entry.safeChangeSummaryJson(),
                LocalDateTime.ofInstant(entry.occurredAt(), ZoneOffset.UTC),
                LocalDateTime.ofInstant(entry.occurredAt(), ZoneOffset.UTC));
    }

    private static Long nullableLong(
            java.sql.ResultSet resultSet,
            String column) throws java.sql.SQLException {
        long value = resultSet.getLong(column);
        return resultSet.wasNull() ? null : value;
    }
}
