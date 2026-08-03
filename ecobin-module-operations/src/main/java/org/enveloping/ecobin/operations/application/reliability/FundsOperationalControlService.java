package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDateTime;
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
    public int wakePayoutTasks(LocalDateTime wakeAt) {
        int pending = jdbc.update("""
                UPDATE ops_reliable_task
                SET next_run_at = ?, wake_version = wake_version + 1,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE execution_lane = 'FUNDS'
                  AND task_type IN (
                      'SUBMIT_MERCHANT_TRANSFER',
                      'QUERY_MERCHANT_TRANSFER'
                  )
                  AND state = 'PENDING'
                """, wakeAt, wakeAt);
        int blocked = jdbc.update("""
                UPDATE ops_reliable_task
                SET state = 'PENDING', next_run_at = ?, completed_at = NULL,
                    blocked_reason_code = NULL, blocked_diagnostic = NULL,
                    consecutive_failure_count = 0,
                    wake_version = wake_version + 1,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE execution_lane = 'FUNDS'
                  AND task_type IN (
                      'SUBMIT_MERCHANT_TRANSFER',
                      'QUERY_MERCHANT_TRANSFER'
                  )
                  AND state = 'BLOCKED'
                """, wakeAt, wakeAt);
        return pending + blocked;
    }

    private static String sourceKey(
            long merchantProfileId,
            UUID pausedEventUid) {
        return "PAYOUT_GATE:" + merchantProfileId + ":" + pausedEventUid;
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
        } catch (Exception failure) {
            throw new IllegalStateException("SHA-256 unavailable", failure);
        }
    }
}
