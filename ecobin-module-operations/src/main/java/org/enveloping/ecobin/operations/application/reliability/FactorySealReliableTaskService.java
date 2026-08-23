package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.FactorySealReliableTaskPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Objects;
import java.util.UUID;

/** Operations-side terminal transitions for the narrow factory-seal task. */
@Service
public class FactorySealReliableTaskService
        implements FactorySealReliableTaskPort {

    private static final String TASK_TYPE = "AUTHORIZE_FACTORY_SEAL";

    private final JdbcTemplate jdbc;

    public FactorySealReliableTaskService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void completeAcceptedCommand(
            UUID commandUid,
            LocalDateTime completedAt) {
        Objects.requireNonNull(commandUid, "commandUid");
        Objects.requireNonNull(completedAt, "completedAt");
        LockedTask task = lockByCommand(commandUid);
        if ("DONE".equals(task.state())
                || "CANCELLED".equals(task.state())) {
            return;
        }
        requireSingle(jdbc.update("""
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
                          AND state IN ('PENDING', 'BLOCKED')
                        """,
                completedAt,
                completedAt,
                task.id()), "complete factory seal task");
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void cancelTask(
            UUID taskUid,
            String reasonCode,
            LocalDateTime cancelledAt) {
        Objects.requireNonNull(taskUid, "taskUid");
        Objects.requireNonNull(cancelledAt, "cancelledAt");
        if (reasonCode == null
                || !reasonCode.matches("[A-Z][A-Z0-9_]{0,63}")) {
            throw new IllegalArgumentException(
                    "factory seal cancellation reason is invalid");
        }
        List<LockedTask> rows = jdbc.query("""
                        SELECT id, state
                        FROM ops_reliable_task
                        WHERE task_uid = ?
                          AND scope_kind = 'PLATFORM'
                          AND task_type = 'AUTHORIZE_FACTORY_SEAL'
                          AND source_device_command_id IS NULL
                        FOR UPDATE
                        """,
                (rs, ignored) -> new LockedTask(
                        rs.getLong("id"), rs.getString("state")),
                taskUid.toString());
        if (rows.size() != 1) {
            throw new ReliableTaskInvariantException(
                    "factory seal cancellation did not resolve one task");
        }
        LockedTask task = rows.getFirst();
        if ("DONE".equals(task.state())
                || "CANCELLED".equals(task.state())) {
            return;
        }
        requireSingle(jdbc.update("""
                        UPDATE ops_reliable_task
                        SET state = 'CANCELLED',
                            next_run_at = NULL,
                            lease_token = NULL,
                            lease_worker = NULL,
                            lease_until = NULL,
                            dispatch_wait_reason = NULL,
                            handled_wake_version = wake_version,
                            completed_at = ?,
                            blocked_reason_code = NULL,
                            blocked_diagnostic = NULL,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND state IN ('PENDING', 'BLOCKED')
                        """,
                cancelledAt,
                cancelledAt,
                task.id()), "cancel factory seal task");
    }

    private LockedTask lockByCommand(UUID commandUid) {
        List<LockedTask> rows = jdbc.query("""
                        SELECT id, state
                        FROM ops_reliable_task
                        WHERE scope_kind = 'PLATFORM'
                          AND task_type = 'AUTHORIZE_FACTORY_SEAL'
                          AND source_device_command_id IS NULL
                          AND CONVERT(
                              JSON_UNQUOTE(JSON_EXTRACT(
                                  redacted_execution_snapshot,
                                  '$.commandUid'
                              )) USING ascii
                          ) COLLATE ascii_bin = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new LockedTask(
                        rs.getLong("id"), rs.getString("state")),
                commandUid.toString());
        if (rows.size() != 1) {
            throw new ReliableTaskInvariantException(
                    "factory seal observation did not resolve one task");
        }
        return rows.getFirst();
    }

    private static void requireSingle(int rows, String action) {
        if (rows != 1) {
            throw new ReliableTaskInvariantException(
                    action + " affected " + rows + " rows");
        }
    }

    private record LockedTask(long id, String state) {
    }
}
