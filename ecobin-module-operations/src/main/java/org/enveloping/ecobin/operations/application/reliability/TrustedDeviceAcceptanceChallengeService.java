package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.TrustedDeviceAcceptanceChallengePort;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

/** Closes only the exact platform challenge proven by an authenticated event. */
@Service
public class TrustedDeviceAcceptanceChallengeService
        implements TrustedDeviceAcceptanceChallengePort {

    private final JdbcTemplate jdbc;

    public TrustedDeviceAcceptanceChallengeService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void consume(
            long assetId,
            UUID commandUid,
            UUID challengeUid,
            LocalDateTime receivedAt) {
        List<ChallengeTask> rows = jdbc.query("""
                        SELECT id, state, wake_version
                        FROM ops_reliable_task
                        WHERE scope_kind = 'PLATFORM'
                          AND tenant_id IS NULL
                          AND organization_id IS NULL
                          AND task_category = 'BUSINESS_INTENT'
                          AND execution_lane = 'DEVICE'
                          AND task_type = 'REQUEST_DEVICE_ACCEPTANCE'
                          AND source_device_asset_id = ?
                          AND target_type = 'DEVICE_ASSET'
                          AND target_stable_key = ?
                          AND JSON_UNQUOTE(JSON_EXTRACT(
                                redacted_execution_snapshot,
                                '$.commandUid')) = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ChallengeTask(
                        rs.getLong("id"),
                        rs.getString("state"),
                        rs.getLong("wake_version")),
                assetId,
                challengeUid.toString(),
                commandUid.toString());
        if (rows.size() != 1) {
            throw new ReliableTaskInvariantException(
                    "acceptance evidence does not match a platform challenge");
        }
        ChallengeTask task = rows.getFirst();
        if ("CANCELLED".equals(task.state())) {
            throw new ReliableTaskInvariantException(
                    "cancelled acceptance challenge cannot be consumed");
        }
        if ("DONE".equals(task.state())) {
            return;
        }
        int updated = jdbc.update("""
                        UPDATE ops_reliable_task
                        SET state = 'DONE',
                            next_run_at = NULL,
                            lease_token = NULL,
                            lease_worker = NULL,
                            lease_until = NULL,
                            dispatch_wait_reason = NULL,
                            consecutive_failure_count = 0,
                            handled_wake_version = wake_version,
                            completed_at = ?,
                            blocked_reason_code = NULL,
                            blocked_diagnostic = NULL,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                        """,
                receivedAt,
                receivedAt,
                task.id());
        if (updated != 1) {
            throw new IllegalStateException(
                    "consume acceptance challenge updated "
                            + updated + " rows");
        }
    }

    private record ChallengeTask(long id, String state, long wakeVersion) {
    }
}
