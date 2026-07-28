package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Objects;

@Service
public class ReliableDeviceTaskProofService
        implements ReliableDeviceTaskProofPort {

    private final JdbcTemplate jdbc;

    public ReliableDeviceTaskProofService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void completeFromTrustedProof(
            String taskType,
            String targetType,
            String targetStableKey) {
        Objects.requireNonNull(taskType, "taskType");
        Objects.requireNonNull(targetType, "targetType");
        Objects.requireNonNull(targetStableKey, "targetStableKey");
        List<LockedTask> rows = jdbc.query("""
                        SELECT id, state
                        FROM ops_reliable_task
                        WHERE task_type = ?
                          AND target_type = ?
                          AND target_stable_key = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new LockedTask(
                        rs.getLong("id"),
                        rs.getString("state")),
                taskType,
                targetType,
                targetStableKey);
        if (rows.size() != 1) {
            throw new ReliableTaskInvariantException(
                    "trusted device proof did not resolve one reliable task");
        }
        LockedTask task = rows.getFirst();
        if ("DONE".equals(task.state())
                || "CANCELLED".equals(task.state())) {
            return;
        }
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        int updated = jdbc.update("""
                        UPDATE ops_reliable_task
                        SET state = 'DONE',
                            next_run_at = NULL,
                            lease_token = NULL,
                            lease_worker = NULL,
                            lease_until = NULL,
                            handled_wake_version = wake_version,
                            completed_at = ?,
                            blocked_reason_code = NULL,
                            blocked_diagnostic = NULL,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND state IN ('PENDING', 'BLOCKED')
                        """,
                now,
                now,
                task.taskId());
        if (updated != 1) {
            throw new ReliableTaskInvariantException(
                    "trusted device proof could not complete its task");
        }
    }

    private record LockedTask(
            long taskId,
            String state) {
    }
}
