package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.TrustedDeviceAcceptanceChallengePort;
import org.enveloping.ecobin.device.api.result.DeviceAcceptanceChallengeConsumeResult;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.List;
import java.util.Objects;
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
    public DeviceAcceptanceChallengeConsumeResult consume(
            long assetId,
            UUID commandUid,
            UUID challengeUid,
            long factoryBagRevision,
            byte[] factoryBagSetSha256,
            LocalDateTime receivedAt) {
        List<ChallengeTask> rows = jdbc.query("""
                        SELECT id, state, wake_version,
                               CAST(JSON_UNQUOTE(JSON_EXTRACT(
                                   redacted_execution_snapshot,
                                   '$.payload.factoryBagRevision'
                               )) AS UNSIGNED) AS factory_bag_revision,
                               JSON_UNQUOTE(JSON_EXTRACT(
                                   redacted_execution_snapshot,
                                   '$.payload.factoryBagSetSha256'
                               )) AS factory_bag_set_sha256
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
                        rs.getLong("wake_version"),
                        rs.getLong("factory_bag_revision"),
                        rs.getString("factory_bag_set_sha256")),
                assetId,
                challengeUid.toString(),
                commandUid.toString());
        if (rows.size() != 1) {
            throw new ReliableTaskInvariantException(
                    "acceptance evidence does not match a platform challenge");
        }
        ChallengeTask task = rows.getFirst();
        if (!sameFactoryBagSnapshot(
                task.factoryBagRevision(),
                task.factoryBagSetSha256(),
                factoryBagRevision,
                factoryBagSetSha256)) {
            throw new ReliableTaskInvariantException(
                    "acceptance evidence differs from its factory bag snapshot");
        }
        if ("CANCELLED".equals(task.state())) {
            return DeviceAcceptanceChallengeConsumeResult.CANCELLED;
        }
        if ("DONE".equals(task.state())) {
            return DeviceAcceptanceChallengeConsumeResult.CONSUMED;
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
        return DeviceAcceptanceChallengeConsumeResult.CONSUMED;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void cancelOutstanding(long assetId, LocalDateTime cancelledAt) {
        jdbc.update("""
                        UPDATE ops_reliable_task
                        SET state = 'CANCELLED',
                            next_run_at = NULL,
                            lease_token = NULL,
                            lease_worker = NULL,
                            lease_until = NULL,
                            dispatch_wait_reason = NULL,
                            handled_wake_version = wake_version,
                            completed_at = COALESCE(completed_at, ?),
                            blocked_reason_code = NULL,
                            blocked_diagnostic = NULL,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE scope_kind = 'PLATFORM'
                          AND tenant_id IS NULL
                          AND organization_id IS NULL
                          AND task_type = 'REQUEST_DEVICE_ACCEPTANCE'
                          AND source_device_asset_id = ?
                          AND state IN ('PENDING', 'BLOCKED')
                        """,
                cancelledAt,
                cancelledAt,
                assetId);
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void cancelExpiredBlocked(
            long assetId, LocalDateTime cancelledAt) {
        jdbc.update("""
                        UPDATE ops_reliable_task
                        SET state = 'CANCELLED',
                            next_run_at = NULL,
                            lease_token = NULL,
                            lease_worker = NULL,
                            lease_until = NULL,
                            dispatch_wait_reason = NULL,
                            handled_wake_version = wake_version,
                            completed_at = COALESCE(completed_at, ?),
                            blocked_reason_code = NULL,
                            blocked_diagnostic = NULL,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE scope_kind = 'PLATFORM'
                          AND tenant_id IS NULL
                          AND organization_id IS NULL
                          AND task_type = 'REQUEST_DEVICE_ACCEPTANCE'
                          AND source_device_asset_id = ?
                          AND state = 'BLOCKED'
                          AND lease_token IS NULL
                          AND STR_TO_DATE(
                              JSON_UNQUOTE(JSON_EXTRACT(
                                  redacted_execution_snapshot,
                                  '$.expiresAt')),
                              '%Y-%m-%dT%H:%i:%s.%fZ') <= ?
                        """,
                cancelledAt,
                cancelledAt,
                assetId,
                cancelledAt);
    }

    static boolean sameFactoryBagSnapshot(
            long taskRevision,
            String taskDigest,
            long evidenceRevision,
            byte[] evidenceDigest) {
        String suppliedDigest = evidenceDigest == null
                ? null : HexFormat.of().formatHex(evidenceDigest);
        return taskRevision == evidenceRevision
                && Objects.equals(taskDigest, suppliedDigest);
    }

    private record ChallengeTask(
            long id,
            String state,
            long wakeVersion,
            long factoryBagRevision,
            String factoryBagSetSha256) {
    }
}
