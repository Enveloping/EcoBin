package org.enveloping.ecobin.operations.application.governance;

import tools.jackson.databind.ObjectMapper;
import org.enveloping.ecobin.device.api.port.DeviceOperationalAlertSourcePort;
import org.enveloping.ecobin.device.api.result.DeviceFaultAlertFact;
import org.enveloping.ecobin.recycling.api.port.RecyclingOperationalAlertSourcePort;
import org.enveloping.ecobin.recycling.api.result.PortFullnessAlertFact;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Map;
import java.util.UUID;

/**
 * Materializes domain and technical conditions into the operations-owned alert
 * projection. Source facts remain authoritative; acknowledgement never affects
 * this synchronization.
 */
@Service
@ConditionalOnProperty(
        prefix = "ecobin.operations.reliable",
        name = "workers-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class OperationalAlertProjectionService {

    private final JdbcTemplate jdbc;
    private final DeviceOperationalAlertSourcePort deviceSource;
    private final RecyclingOperationalAlertSourcePort recyclingSource;
    private final ObjectMapper objectMapper;

    public OperationalAlertProjectionService(
            JdbcTemplate jdbc,
            DeviceOperationalAlertSourcePort deviceSource,
            RecyclingOperationalAlertSourcePort recyclingSource,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.deviceSource = deviceSource;
        this.recyclingSource = recyclingSource;
        this.objectMapper = objectMapper;
    }

    @Scheduled(
            initialDelayString =
                    "${ecobin.operations.alert-projection-initial-delay-ms:5000}",
            fixedDelayString =
                    "${ecobin.operations.alert-projection-interval-ms:30000}")
    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public void project() {
        LocalDateTime now = databaseNow();
        projectReliableTasks(now);
        for (DeviceFaultAlertFact fault
                : deviceSource.loadDeviceFaultAlertFacts(
                openDeviceFaultAlertUids())) {
            projectDeviceFault(fault, now);
        }
        for (PortFullnessAlertFact fullness
                : recyclingSource.loadPortFullnessAlertFacts()) {
            projectFullness(fullness, now);
        }
    }

    private java.util.List<UUID> openDeviceFaultAlertUids() {
        return jdbc.query("""
                        SELECT source_key
                        FROM ops_alert
                        WHERE source_kind = 'DOMAIN_FACT'
                          AND source_type = 'DEVICE_FAULT'
                          AND status = 'OPEN'
                        ORDER BY id
                        """,
                (rs, ignored) -> UUID.fromString(
                        rs.getString("source_key")));
    }

    private void projectReliableTasks(LocalDateTime now) {
        jdbc.update("""
                INSERT INTO ops_alert (
                    alert_uid, scope_kind, tenant_id, organization_id,
                    alert_code, category, current_severity, highest_severity,
                    source_kind, source_type, source_key, aggregation_key,
                    status, first_seen_at, last_seen_at, discovery_count,
                    safe_display_parameters, acknowledged_at,
                    acknowledged_audit_id, resolved_at, lock_version,
                    created_at, updated_at
                )
                SELECT LOWER(CONCAT(
                           HEX(RANDOM_BYTES(4)), '-',
                           HEX(RANDOM_BYTES(2)), '-4',
                           SUBSTRING(HEX(RANDOM_BYTES(2)), 2, 3), '-8',
                           SUBSTRING(HEX(RANDOM_BYTES(2)), 2, 3), '-',
                           HEX(RANDOM_BYTES(6)))),
                       task.scope_kind, task.tenant_id,
                       task.organization_id,
                       'OPERATIONS.RELIABLE_TASK_BLOCKED',
                       'RELIABLE_EXECUTION', 'CRITICAL', 'CRITICAL',
                       'TECHNICAL_CONDITION', 'RELIABLE_TASK',
                       CONCAT(task.task_uid, ':', task.wake_version),
                       UNHEX(SHA2(CONCAT(
                           'RELIABLE_TASK:', task.task_uid), 256)),
                       'OPEN', task.completed_at, task.completed_at, 1,
                       JSON_OBJECT(
                           'taskUid', task.task_uid,
                           'taskType', task.task_type,
                           'reasonCode', task.blocked_reason_code),
                       NULL, NULL, NULL, 0,
                       task.completed_at, task.completed_at
                FROM ops_reliable_task task
                WHERE task.state = 'BLOCKED'
                ON DUPLICATE KEY UPDATE
                    safe_display_parameters = VALUES(safe_display_parameters),
                    updated_at = GREATEST(
                        ops_alert.updated_at, VALUES(updated_at))
                """);
        jdbc.update("""
                UPDATE ops_alert alert
                JOIN ops_reliable_task task
                  ON alert.aggregation_key = UNHEX(SHA2(CONCAT(
                     'RELIABLE_TASK:', task.task_uid), 256))
                SET alert.status = 'RESOLVED',
                    alert.current_severity = 'INFO',
                    alert.resolved_at = ?,
                    alert.lock_version = alert.lock_version + 1,
                    alert.updated_at = ?
                WHERE alert.source_kind = 'TECHNICAL_CONDITION'
                  AND alert.source_type = 'RELIABLE_TASK'
                  AND alert.status = 'OPEN'
                  AND task.state IN ('DONE', 'CANCELLED')
                """, now, now);
    }

    private void projectDeviceFault(
            DeviceFaultAlertFact fault, LocalDateTime now) {
        byte[] aggregation = sha256("DEVICE_FAULT|"
                + fault.deploymentCode() + '|'
                + (fault.portNo() == null ? "DEVICE" : fault.portNo()) + '|'
                + fault.componentType() + '|' + fault.faultCode());
        if (!"OPEN".equals(fault.state())) {
            resolve("DEVICE_FAULT", fault.faultUid().toString(),
                    fault.recoveredAt(), now);
            return;
        }
        String severity = "SAFETY_BLOCKING".equals(fault.impactLevel())
                ? "CRITICAL" : "WARNING";
        fault.scopeRef().withScopeOnce((tenantId, organizationId) -> {
        upsert(
                tenantId, organizationId,
                "DEVICE.FAULT_OPEN", "DEVICE", severity,
                "DOMAIN_FACT", "DEVICE_FAULT",
                fault.faultUid().toString(), aggregation,
                fault.firstDetectedAt(), fault.lastDetectedAt(),
                Map.of(
                        "deploymentCode", fault.deploymentCode(),
                        "portNo", fault.portNo() == null
                                ? "DEVICE" : fault.portNo(),
                        "componentType", fault.componentType(),
                        "faultCode", fault.faultCode(),
                        "impactLevel", fault.impactLevel()),
                now);
            return null;
        });
    }

    private void projectFullness(
            PortFullnessAlertFact fullness, LocalDateTime now) {
        byte[] aggregation = sha256("PORT_FULLNESS|"
                + fullness.deploymentCode() + '|' + fullness.portNo());
        if (!"FULL".equals(fullness.state())) {
            resolveAggregation("PORT_FULLNESS", aggregation,
                    fullness.reportedAt(), now);
            return;
        }
        String sourceKey = fullness.stateChangeUid() == null
                ? "PORT:" + fullness.deploymentCode() + ':'
                + fullness.portNo() + ':' + fullness.reportedAt().toEpochMilli()
                : fullness.stateChangeUid().toString();
        fullness.scopeRef().withScopeOnce((tenantId, organizationId) -> {
        upsert(
                tenantId, organizationId,
                "FULLNESS.PORT_FULL", "FULLNESS", "WARNING",
                "DOMAIN_FACT", "PORT_FULLNESS", sourceKey, aggregation,
                fullness.reportedAt(), fullness.reportedAt(),
                Map.of(
                        "deploymentCode", fullness.deploymentCode(),
                        "portNo", fullness.portNo()),
                now);
            return null;
        });
    }

    private void upsert(
            long tenantId,
            long organizationId,
            String alertCode,
            String category,
            String severity,
            String sourceKind,
            String sourceType,
            String sourceKey,
            byte[] aggregation,
            Instant firstSeenAt,
            Instant lastSeenAt,
            Map<String, ?> parameters,
            LocalDateTime now) {
        LocalDateTime first = utc(firstSeenAt);
        LocalDateTime last = utc(lastSeenAt);
        jdbc.update("""
                INSERT INTO ops_alert (
                    alert_uid, scope_kind, tenant_id, organization_id,
                    alert_code, category, current_severity, highest_severity,
                    source_kind, source_type, source_key, aggregation_key,
                    status, first_seen_at, last_seen_at, discovery_count,
                    safe_display_parameters, acknowledged_at,
                    acknowledged_audit_id, resolved_at, lock_version,
                    created_at, updated_at
                ) VALUES (
                    ?, 'ORGANIZATION', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'OPEN', ?, ?, 1, CAST(? AS JSON),
                    NULL, NULL, NULL, 0, ?, ?
                )
                ON DUPLICATE KEY UPDATE
                    discovery_count = discovery_count
                        + IF(VALUES(last_seen_at) > last_seen_at, 1, 0),
                    lock_version = lock_version
                        + IF(VALUES(last_seen_at) > last_seen_at, 1, 0),
                    last_seen_at = GREATEST(last_seen_at,
                        VALUES(last_seen_at)),
                    current_severity = VALUES(current_severity),
                    highest_severity = CASE
                        WHEN highest_severity = 'CRITICAL'
                          OR VALUES(highest_severity) = 'CRITICAL'
                            THEN 'CRITICAL'
                        WHEN highest_severity = 'WARNING'
                          OR VALUES(highest_severity) = 'WARNING'
                            THEN 'WARNING'
                        ELSE 'INFO'
                    END,
                    safe_display_parameters = VALUES(safe_display_parameters),
                    updated_at = GREATEST(updated_at, VALUES(updated_at))
                """,
                UUID.randomUUID().toString(), tenantId, organizationId,
                alertCode, category, severity, severity,
                sourceKind, sourceType, sourceKey, aggregation,
                first, last, json(parameters), first, now);
    }

    private void resolve(
            String sourceType,
            String sourceKey,
            Instant resolvedAt,
            LocalDateTime now) {
        jdbc.update("""
                UPDATE ops_alert
                SET status = 'RESOLVED', current_severity = 'INFO',
                    resolved_at = ?, lock_version = lock_version + 1,
                    updated_at = ?
                WHERE source_kind = 'DOMAIN_FACT'
                  AND source_type = ? AND source_key = ?
                  AND status = 'OPEN'
                """, utc(resolvedAt), now, sourceType, sourceKey);
    }

    private void resolveAggregation(
            String sourceType,
            byte[] aggregation,
            Instant resolvedAt,
            LocalDateTime now) {
        jdbc.update("""
                UPDATE ops_alert
                SET status = 'RESOLVED', current_severity = 'INFO',
                    resolved_at = ?, lock_version = lock_version + 1,
                    updated_at = ?
                WHERE source_kind = 'DOMAIN_FACT'
                  AND source_type = ? AND aggregation_key = ?
                  AND status = 'OPEN'
                """, utc(resolvedAt), now, sourceType, aggregation);
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private String json(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "alert display parameters could not be encoded",
                    exception);
        }
    }

    private static LocalDateTime utc(Instant value) {
        return LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(
                    value.getBytes(StandardCharsets.UTF_8));
        } catch (Exception exception) {
            throw new IllegalStateException("SHA-256 unavailable", exception);
        }
    }
}
