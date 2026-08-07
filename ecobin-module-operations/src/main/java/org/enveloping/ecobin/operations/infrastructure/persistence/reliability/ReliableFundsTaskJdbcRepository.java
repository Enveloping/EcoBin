package org.enveloping.ecobin.operations.infrastructure.persistence.reliability;

import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort.ReliableFundsTaskRegistration;
import org.enveloping.ecobin.operations.application.reliability.ClaimedFundsTask;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

@Repository
public class ReliableFundsTaskJdbcRepository {

    private final JdbcTemplate jdbc;

    public ReliableFundsTaskJdbcRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    public UUID insert(ReliableFundsTaskRegistration task) {
        UUID uid = UUID.randomUUID();
        LocalDateTime now = databaseNow();
        LocalDateTime initialRunAt = task.initialRunAt() == null
                || task.initialRunAt().isBefore(now)
                ? now : task.initialRunAt();
        try {
            jdbc.update("""
                INSERT INTO ops_reliable_task (
                    task_uid, scope_kind, tenant_id, organization_id,
                    task_category, task_type, execution_lane, task_key,
                    target_type, target_stable_key,
                    source_inbox_id, source_device_asset_id,
                    source_device_command_id, payload_schema_version,
                    redacted_execution_snapshot, payload_sha256,
                    correlation_uid, causation_uid, initiating_audit_id,
                    priority, retry_policy_version, max_auto_attempts,
                    state, next_run_at, lease_token, lease_worker, lease_until,
                    attempt_sequence, consecutive_failure_count, wake_version,
                    handled_wake_version, completed_at, blocked_reason_code,
                    blocked_diagnostic, lock_version, created_at, updated_at
                ) VALUES (
                    ?, 'ORGANIZATION', ?, ?,
                    'BUSINESS_INTENT', ?, 'FUNDS', ?,
                    ?, ?,
                    NULL, NULL, NULL, ?,
                    CAST(? AS JSON), ?,
                    NULL, NULL, NULL,
                    100, 1, ?,
                    'PENDING', ?, NULL, NULL, NULL,
                    0, 0, 0, 0, NULL, NULL, NULL, 0, ?, ?
                )
                """,
                uid.toString(), task.tenantId(), task.organizationId(),
                task.taskType(), task.taskKey(), task.targetType(),
                task.targetStableKey(), task.payloadSchemaVersion(),
                task.redactedExecutionSnapshot(), task.payloadSha256(),
                task.maxAutoAttempts(), initialRunAt, now, now);
        } catch (DuplicateKeyException ignored) {
            // The exact immutable intent is verified below. Other database
            // violations must not be downgraded to an idempotent collision.
        }
        List<RegisteredTask> registered = jdbc.query("""
                SELECT task_uid, tenant_id, organization_id, task_type,
                       target_type, target_stable_key, payload_schema_version,
                       payload_sha256
                FROM ops_reliable_task WHERE task_key = ?
                """, (rs, ignored) -> new RegisteredTask(
                        UUID.fromString(rs.getString("task_uid")),
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getString("task_type"),
                        rs.getString("target_type"),
                        rs.getString("target_stable_key"),
                        rs.getInt("payload_schema_version"),
                        rs.getBytes("payload_sha256")), task.taskKey());
        if (registered.size() != 1
                || !registered.getFirst().sameIntent(task)) {
            throw new IllegalStateException(
                    "reliable funds task key collides with another intent");
        }
        return registered.getFirst().taskUid();
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public ClaimedFundsTask claimOne(
            String workerId, Duration leaseDuration) {
        LocalDateTime now = databaseNow();
        List<Candidate> rows = jdbc.query("""
                SELECT id, task_uid, tenant_id, organization_id,
                       lease_token AS previous_lease_token,
                       attempt_sequence, wake_version,
                       consecutive_failure_count, max_auto_attempts,
                       task_type, target_stable_key
                FROM ops_reliable_task FORCE INDEX (ix_ops_task_claim)
                WHERE state = 'PENDING'
                  AND task_category IN ('BUSINESS_INTENT', 'TIMER')
                  AND execution_lane = 'FUNDS'
                  AND dispatch_wait_reason IS NULL
                  AND claimable_at <= UTC_TIMESTAMP(3)
                ORDER BY claimable_at, priority, id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """, this::candidate);
        if (rows.isEmpty()) {
            return null;
        }
        Candidate row = rows.getFirst();
        if (row.previousLeaseToken() != null) {
            jdbc.update("""
                    UPDATE ops_task_attempt
                    SET reclaimed_at = ?
                    WHERE lease_token = ? AND reclaimed_at IS NULL
                    """, now, row.previousLeaseToken().toString());
        }
        UUID lease = UUID.randomUUID();
        UUID attemptUid = UUID.randomUUID();
        long attemptNo = row.attemptSequence() + 1;
        LocalDateTime leaseUntil = now.plus(leaseDuration);
        requireOne(jdbc.update("""
                UPDATE ops_reliable_task
                SET lease_token = ?, lease_worker = ?, lease_until = ?,
                    attempt_sequence = ?, lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ? AND state = 'PENDING'
                """, lease.toString(), workerId, leaseUntil,
                attemptNo, now, row.taskId()), "claim funds task");
        jdbc.update("""
                INSERT INTO ops_task_attempt (
                    attempt_uid, task_id, scope_kind, tenant_id,
                    organization_id, attempt_no, lease_token,
                    claimed_wake_version, worker_id, claimed_at, lease_until,
                    external_call_may_have_started_at, reclaimed_at,
                    result_recorded_at, action_kind, technical_result,
                    request_sha256, response_sha256, http_status,
                    external_api_error_code, duration_ms,
                    redacted_diagnostic, created_at
                ) VALUES (
                    ?, ?, 'ORGANIZATION', ?, ?, ?, ?, ?, ?, ?, ?,
                    NULL, NULL, NULL, ?, NULL,
                    NULL, NULL, NULL, NULL, NULL, NULL, ?
                )
                """, attemptUid.toString(), row.taskId(), row.tenantId(),
                row.organizationId(), attemptNo, lease.toString(),
                row.wakeVersion(), workerId, now, leaseUntil,
                actionKind(row.taskType()), now);
        Long attemptId = jdbc.queryForObject(
                "SELECT id FROM ops_task_attempt WHERE attempt_uid = ?",
                Long.class, attemptUid.toString());
        return new ClaimedFundsTask(
                row.taskId(), row.taskUid(), attemptId, attemptUid, lease,
                row.wakeVersion(), row.consecutiveFailureCount(),
                row.maxAutoAttempts(), row.taskType(),
                row.targetStableKey(), now);
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public void markExternalCallMayHaveStarted(ClaimedFundsTask task) {
        requireOne(jdbc.update("""
                UPDATE ops_task_attempt
                SET external_call_may_have_started_at = UTC_TIMESTAMP(3)
                WHERE id = ? AND lease_token = ?
                  AND external_call_may_have_started_at IS NULL
                  AND result_recorded_at IS NULL
                """, task.attemptId(), task.leaseToken().toString()),
                "mark funds call boundary");
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public void markExternalCallMayHaveStarted(UUID attemptUid) {
        requireOne(jdbc.update("""
                UPDATE ops_task_attempt
                SET external_call_may_have_started_at = UTC_TIMESTAMP(3)
                WHERE attempt_uid = ?
                  AND external_call_may_have_started_at IS NULL
                  AND result_recorded_at IS NULL
                """, attemptUid.toString()),
                "mark funds call boundary");
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public void complete(
            ClaimedFundsTask claim,
            String technicalResult,
            String diagnostic,
            boolean done,
            boolean blocked,
            Duration retryAfter,
            long durationMillis) {
        LocalDateTime now = databaseNow();
        List<Long> current = jdbc.query("""
                SELECT id FROM ops_reliable_task
                WHERE id = ? AND task_uid = ? AND state = 'PENDING'
                  AND lease_token = ? AND wake_version = ?
                FOR UPDATE
                """, (rs, ignored) -> rs.getLong("id"),
                claim.taskId(), claim.taskUid().toString(),
                claim.leaseToken().toString(), claim.claimedWakeVersion());
        if (current.size() != 1) {
            return;
        }
        jdbc.update("""
                UPDATE ops_task_attempt
                SET result_recorded_at = ?, technical_result = ?,
                    duration_ms = ?, redacted_diagnostic = ?
                WHERE id = ? AND result_recorded_at IS NULL
                """, now, technicalResult, Math.max(0, durationMillis),
                safeDiagnostic(diagnostic), claim.attemptId());
        if (done) {
            requireOne(jdbc.update("""
                    UPDATE ops_reliable_task
                    SET state = 'DONE', next_run_at = NULL,
                        lease_token = NULL, lease_worker = NULL,
                        lease_until = NULL, handled_wake_version = wake_version,
                        completed_at = ?, consecutive_failure_count = 0,
                        blocked_reason_code = NULL, blocked_diagnostic = NULL,
                        dispatch_wait_reason = NULL,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ?
                    """, now, now, claim.taskId()), "complete funds task");
            return;
        }
        int failures = claim.consecutiveFailureCount()
                + ("RETRYABLE_FAILURE".equals(technicalResult) ? 1 : 0);
        if (blocked || failures >= claim.maxAutoAttempts()) {
            requireOne(jdbc.update("""
                    UPDATE ops_reliable_task
                    SET state = 'BLOCKED', next_run_at = NULL,
                        lease_token = NULL, lease_worker = NULL,
                        lease_until = NULL, handled_wake_version = wake_version,
                        completed_at = ?, consecutive_failure_count = ?,
                        blocked_reason_code = ?, blocked_diagnostic = ?,
                        dispatch_wait_reason = NULL,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ?
                    """, now, failures,
                    blocked ? "FUNDS_TASK_BLOCKED" : "AUTO_RETRY_EXHAUSTED",
                    safeDiagnostic(diagnostic), now, claim.taskId()),
                    "block funds task");
            return;
        }
        requireOne(jdbc.update("""
                UPDATE ops_reliable_task
                SET next_run_at = ?, lease_token = NULL,
                    lease_worker = NULL, lease_until = NULL,
                    consecutive_failure_count = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ?
                """, now.plus(retryAfter), failures, now, claim.taskId()),
                "retry funds task");
    }

    private Candidate candidate(ResultSet rs, int ignored) throws SQLException {
        String previous = rs.getString("previous_lease_token");
        return new Candidate(
                rs.getLong("id"),
                UUID.fromString(rs.getString("task_uid")),
                rs.getLong("tenant_id"),
                rs.getLong("organization_id"),
                previous == null ? null : UUID.fromString(previous),
                rs.getLong("attempt_sequence"),
                rs.getLong("wake_version"),
                rs.getInt("consecutive_failure_count"),
                rs.getInt("max_auto_attempts"),
                rs.getString("task_type"),
                rs.getString("target_stable_key"));
    }

    private static String actionKind(String taskType) {
        if (taskType.startsWith("QUERY_")) return "QUERY";
        if (taskType.startsWith("CLOSE_")) return "CLOSE";
        if (taskType.startsWith("CANCEL_")) return "CANCEL";
        if (taskType.startsWith("POST_")) return "PROCESS";
        return "SUBMIT";
    }

    private static String safeDiagnostic(String value) {
        if (value == null || value.isBlank()) return null;
        return value.length() <= 1000 ? value : value.substring(0, 1000);
    }

    private static void requireOne(int count, String operation) {
        if (count != 1) {
            throw new IllegalStateException(operation + " affected " + count + " rows");
        }
    }

    private record Candidate(
            long taskId, UUID taskUid, long tenantId, long organizationId,
            UUID previousLeaseToken, long attemptSequence, long wakeVersion,
            int consecutiveFailureCount, int maxAutoAttempts,
            String taskType, String targetStableKey) {
    }

    private record RegisteredTask(
            UUID taskUid, long tenantId, long organizationId,
            String taskType, String targetType, String targetStableKey,
            int payloadSchemaVersion, byte[] payloadSha256) {

        boolean sameIntent(ReliableFundsTaskRegistration task) {
            return tenantId == task.tenantId()
                    && organizationId == task.organizationId()
                    && taskType.equals(task.taskType())
                    && targetType.equals(task.targetType())
                    && targetStableKey.equals(task.targetStableKey())
                    && payloadSchemaVersion == task.payloadSchemaVersion()
                    && java.util.Arrays.equals(
                    payloadSha256, task.payloadSha256());
        }
    }
}
