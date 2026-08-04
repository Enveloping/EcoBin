package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort;
import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort.ReconciliationIssue;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Objects;
import java.util.UUID;

@Service
public class FundsOperationalControlService
        implements FundsOperationalControlPort {

    private final JdbcTemplate jdbc;

    public FundsOperationalControlService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void observePayoutLiquidityPause(
            long merchantProfileId,
            UUID pausedEventUid,
            LocalDateTime observedAt) {
        String sourceKey = sourceKey(merchantProfileId, pausedEventUid);
        String safeParameters = "{\"merchantProfileId\":"
                + merchantProfileId + ",\"pausedEventUid\":\""
                + pausedEventUid + "\"}";
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
                    ?, 'PLATFORM', NULL, NULL,
                    'FUNDS.PAYOUT_NOT_ENOUGH', 'FUNDS',
                    'CRITICAL', 'CRITICAL', 'DOMAIN_FACT',
                    'PAYOUT_GATE_PAUSE', ?, ?, 'OPEN', ?, ?, 1,
                    CAST(? AS JSON), NULL, NULL, NULL, 0, ?, ?
                )
                ON DUPLICATE KEY UPDATE
                    last_seen_at = VALUES(last_seen_at),
                    discovery_count = discovery_count + 1,
                    current_severity = 'CRITICAL',
                    highest_severity = 'CRITICAL',
                    lock_version = lock_version + 1,
                    updated_at = VALUES(updated_at)
                """, UUID.randomUUID().toString(), sourceKey,
                sha256("PAYOUT_GATE:" + merchantProfileId), observedAt,
                observedAt, safeParameters, observedAt, observedAt);
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void resolvePayoutLiquidityPause(
            long merchantProfileId,
            UUID pausedEventUid,
            LocalDateTime resolvedAt) {
        jdbc.update("""
                UPDATE ops_alert
                SET status = 'RESOLVED', current_severity = 'INFO',
                    resolved_at = ?, lock_version = lock_version + 1,
                    updated_at = ?
                WHERE scope_kind = 'PLATFORM'
                  AND source_kind = 'DOMAIN_FACT'
                  AND source_type = 'PAYOUT_GATE_PAUSE'
                  AND source_key = ? AND status = 'OPEN'
                """, resolvedAt, resolvedAt,
                sourceKey(merchantProfileId, pausedEventUid));
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void markPayoutTaskWaiting(
            UUID taskUid,
            long merchantProfileId,
            UUID pausedEventUid,
            LocalDateTime waitingAt) {
        int updated = jdbc.update("""
                UPDATE ops_reliable_task task
                JOIN fund_withdrawal_order withdrawal
                  ON task.target_type = 'WITHDRAWAL_ORDER'
                 AND task.target_stable_key =
                     withdrawal.withdrawal_order_no
                SET task.dispatch_wait_reason = ?,
                    task.next_run_at = ?,
                    task.lock_version = task.lock_version + 1,
                    task.updated_at = ?
                WHERE task.task_uid = ?
                  AND task.execution_lane = 'FUNDS'
                  AND task.task_type = 'SUBMIT_MERCHANT_TRANSFER'
                  AND task.state = 'PENDING'
                  AND withdrawal.merchant_profile_id = ?
                """, payoutWaitReason(pausedEventUid),
                waitingAt.plusDays(30), waitingAt, taskUid.toString(),
                merchantProfileId);
        if (updated != 1) {
            throw new IllegalStateException(
                    "payout waiting task did not match its merchant");
        }
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public int wakePayoutTasks(
            long merchantProfileId,
            UUID pausedEventUid,
            LocalDateTime wakeAt) {
        return jdbc.update("""
                UPDATE ops_reliable_task task
                JOIN fund_withdrawal_order withdrawal
                  ON task.target_type = 'WITHDRAWAL_ORDER'
                 AND task.target_stable_key =
                     withdrawal.withdrawal_order_no
                SET task.dispatch_wait_reason = NULL,
                    task.next_run_at = ?,
                    task.wake_version = task.wake_version + 1,
                    task.lock_version = task.lock_version + 1,
                    task.updated_at = ?
                WHERE task.execution_lane = 'FUNDS'
                  AND task.task_type = 'SUBMIT_MERCHANT_TRANSFER'
                  AND task.state = 'PENDING'
                  AND task.dispatch_wait_reason = ?
                  AND withdrawal.merchant_profile_id = ?
                """, wakeAt, wakeAt, payoutWaitReason(pausedEventUid),
                merchantProfileId);
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public WithdrawalSubmitTaskWakeResult wakeWithdrawalSubmitTask(
            long tenantId,
            long organizationId,
            String withdrawalNo,
            LocalDateTime wakeAt) {
        if (tenantId <= 0 || organizationId <= 0) {
            throw new IllegalArgumentException(
                    "withdrawal task scope must be positive");
        }
        if (withdrawalNo == null || withdrawalNo.isBlank()) {
            throw new IllegalArgumentException(
                    "withdrawalNo must not be blank");
        }
        Objects.requireNonNull(wakeAt, "wakeAt");
        List<WithdrawalSubmitTask> tasks = jdbc.query("""
                SELECT id, state, lease_token, dispatch_wait_reason,
                       wake_version
                FROM ops_reliable_task
                WHERE scope_kind = 'ORGANIZATION'
                  AND tenant_id = ? AND organization_id = ?
                  AND execution_lane = 'FUNDS'
                  AND task_type = 'SUBMIT_MERCHANT_TRANSFER'
                  AND target_type = 'WITHDRAWAL_ORDER'
                  AND target_stable_key = ?
                FOR UPDATE
                """,
                (resultSet, ignored) -> new WithdrawalSubmitTask(
                        resultSet.getLong("id"),
                        resultSet.getString("state"),
                        resultSet.getString("lease_token"),
                        resultSet.getString("dispatch_wait_reason"),
                        resultSet.getLong("wake_version")),
                tenantId, organizationId, withdrawalNo);
        if (tasks.size() != 1) {
            return WithdrawalSubmitTaskWakeResult.NOT_WAKEABLE;
        }
        WithdrawalSubmitTask task = tasks.getFirst();
        if (!"PENDING".equals(task.state())) {
            return WithdrawalSubmitTaskWakeResult.NOT_WAKEABLE;
        }
        if (task.dispatchWaitReason() != null) {
            return WithdrawalSubmitTaskWakeResult
                    .WAITING_ON_ANOTHER_CONDITION;
        }
        if (task.wakeVersion() >= 9_007_199_254_740_991L) {
            return WithdrawalSubmitTaskWakeResult.NOT_WAKEABLE;
        }
        long nextWakeVersion = task.wakeVersion() + 1;
        int updated;
        if (task.leaseToken() == null) {
            updated = jdbc.update("""
                    UPDATE ops_reliable_task
                    SET next_run_at = ?, wake_version = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ? AND state = 'PENDING'
                      AND lease_token IS NULL
                      AND dispatch_wait_reason IS NULL
                      AND wake_version = ?
                    """, wakeAt, nextWakeVersion, wakeAt,
                    task.id(), task.wakeVersion());
        } else {
            updated = jdbc.update("""
                    UPDATE ops_reliable_task
                    SET wake_version = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ? AND state = 'PENDING'
                      AND lease_token = ?
                      AND dispatch_wait_reason IS NULL
                      AND wake_version = ?
                    """, nextWakeVersion, wakeAt, task.id(),
                    task.leaseToken(), task.wakeVersion());
        }
        if (updated != 1) {
            throw new IllegalStateException(
                    "precise withdrawal submit wake updated "
                            + updated + " rows");
        }
        return WithdrawalSubmitTaskWakeResult.WOKEN;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void observeReconciliationIssue(ReconciliationIssue issue) {
        byte[] dedupe = sha256(issue.issueCode() + "|"
                + issue.subjectType() + "|" + issue.subjectStableKey());
        jdbc.update("""
                INSERT INTO ops_reconciliation_issue (
                    issue_uid, scope_kind, tenant_id, organization_id,
                    issue_code, severity, subject_type, subject_stable_key,
                    dedupe_key, state, first_seen_run_id,
                    latest_seen_run_id, first_seen_task_attempt_id,
                    latest_seen_task_attempt_id, first_seen_at, last_seen_at,
                    discovery_count, initial_evidence_sha256,
                    redacted_evidence_summary, last_handled_at,
                    last_handled_audit_id, system_verified_resolved_at,
                    lock_version, created_at, updated_at
                ) VALUES (
                    ?, 'ORGANIZATION', ?, ?, ?, ?, ?, ?, ?, 'UNRESOLVED',
                    NULL, NULL, ?, ?, ?, ?, 1, ?, ?, NULL, NULL, NULL,
                    0, ?, ?
                )
                ON DUPLICATE KEY UPDATE
                    severity = VALUES(severity),
                    latest_seen_run_id = NULL,
                    latest_seen_task_attempt_id =
                        VALUES(latest_seen_task_attempt_id),
                    last_seen_at = VALUES(last_seen_at),
                    discovery_count = discovery_count + 1,
                    lock_version = lock_version + 1,
                    updated_at = VALUES(updated_at)
                """, UUID.randomUUID().toString(), issue.tenantId(),
                issue.organizationId(), issue.issueCode(), issue.severity(),
                issue.subjectType(), issue.subjectStableKey(), dedupe,
                issue.sourceTaskAttemptId(), issue.sourceTaskAttemptId(),
                issue.observedAt(), issue.observedAt(),
                issue.initialEvidenceSha256(),
                trimTo(issue.redactedEvidenceSummary(), 2000),
                issue.observedAt(), issue.observedAt());
    }

    private static String sourceKey(
            long merchantProfileId,
            UUID pausedEventUid) {
        return "PAYOUT_GATE:" + merchantProfileId + ":" + pausedEventUid;
    }

    private static String payoutWaitReason(UUID pausedEventUid) {
        return "PAYOUT_NOT_ENOUGH:" + pausedEventUid;
    }

    private static String trimTo(String value, int length) {
        String trimmed = value.trim();
        return trimmed.length() <= length
                ? trimmed : trimmed.substring(0, length);
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
        } catch (Exception failure) {
            throw new IllegalStateException("SHA-256 unavailable", failure);
        }
    }

    private record WithdrawalSubmitTask(
            long id,
            String state,
            String leaseToken,
            String dispatchWaitReason,
            long wakeVersion) {
    }
}
