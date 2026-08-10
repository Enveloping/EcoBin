package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskStatus;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskStatusPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Service
public class ReliableDeviceTaskStatusService
        implements ReliableDeviceTaskStatusPort {

    private final JdbcTemplate jdbc;

    public ReliableDeviceTaskStatusService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public Optional<ReliableDeviceTaskStatus> find(
            String taskType,
            String targetType,
            String targetStableKey,
            boolean forUpdate) {
        String lock = forUpdate ? " FOR UPDATE" : "";
        List<ReliableDeviceTaskStatus> rows = jdbc.query("""
                        SELECT task_uid, state, blocked_reason_code,
                               wake_version, lock_version
                        FROM ops_reliable_task
                        WHERE task_type = ?
                          AND target_type = ?
                          AND target_stable_key = ?
                        """ + lock,
                (rs, ignored) -> new ReliableDeviceTaskStatus(
                        UUID.fromString(rs.getString("task_uid")),
                        rs.getString("state"),
                        rs.getString("blocked_reason_code"),
                        rs.getLong("wake_version"),
                        rs.getLong("lock_version")),
                taskType,
                targetType,
                targetStableKey);
        return rows.stream().findFirst();
    }
}
