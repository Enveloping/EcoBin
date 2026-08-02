package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.api.reliability.DeviceTelemetryRetentionPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;

@Service
public class DeviceTelemetryRetentionService
        implements DeviceTelemetryRetentionPort {

    private final JdbcTemplate jdbc;

    public DeviceTelemetryRetentionService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public int purgeRuntimeSnapshotsBefore(
            Instant cutoff,
            int batchSize) {
        if (cutoff == null) {
            throw new IllegalArgumentException("cutoff is required");
        }
        if (batchSize < 1 || batchSize > 5_000) {
            throw new IllegalArgumentException(
                    "batchSize must be between 1 and 5000");
        }
        List<Candidate> candidates = jdbc.query("""
                        SELECT inbox.id AS inbox_id,
                               edge.id AS edge_id,
                               task.id AS task_id
                        FROM ops_inbox_message inbox
                        LEFT JOIN dev_edge_event edge
                          ON edge.source_inbox_id = inbox.id
                         AND edge.event_type =
                             'DEVICE_RUNTIME_SNAPSHOT'
                        LEFT JOIN dev_deployment_runtime_state runtime
                          ON runtime.trusted_runtime_edge_event_id = edge.id
                        LEFT JOIN dev_port_runtime_state port_runtime
                          ON port_runtime.trusted_runtime_edge_event_id =
                             edge.id
                        LEFT JOIN ops_reliable_task task
                          ON task.source_inbox_id = inbox.id
                        LEFT JOIN ops_message_quarantine quarantine
                          ON quarantine.conflicting_inbox_id = inbox.id
                        WHERE inbox.message_kind =
                              'DEVICE_RUNTIME_SNAPSHOT'
                          AND inbox.processing_state = 'PROCESSED'
                          AND inbox.processed_at < ?
                          AND runtime.deployment_id IS NULL
                          AND port_runtime.port_id IS NULL
                          AND quarantine.id IS NULL
                          AND (task.id IS NULL OR task.state = 'DONE')
                          AND NOT EXISTS (
                              SELECT 1
                              FROM ops_reconciliation_action action
                              WHERE action.reliable_task_id = task.id
                                 OR action.source_task_attempt_id IN (
                                     SELECT attempt.id
                                     FROM ops_task_attempt attempt
                                     WHERE attempt.task_id = task.id
                                 )
                          )
                        ORDER BY inbox.processed_at, inbox.id
                        LIMIT ?
                        FOR UPDATE SKIP LOCKED
                        """,
                (rs, ignored) -> new Candidate(
                        rs.getLong("inbox_id"),
                        nullableLong(rs, "edge_id"),
                        nullableLong(rs, "task_id")),
                LocalDateTime.ofInstant(cutoff, ZoneOffset.UTC),
                batchSize);
        if (candidates.isEmpty()) {
            return 0;
        }
        deleteByIds(
                "DELETE FROM ops_task_attempt WHERE task_id IN (%s)",
                candidates.stream()
                        .map(Candidate::taskId)
                        .filter(java.util.Objects::nonNull)
                        .toList());
        deleteByIds(
                "DELETE FROM ops_reliable_task WHERE id IN (%s)",
                candidates.stream()
                        .map(Candidate::taskId)
                        .filter(java.util.Objects::nonNull)
                        .toList());
        deleteByIds(
                "DELETE FROM dev_edge_event WHERE id IN (%s)",
                candidates.stream()
                        .map(Candidate::edgeId)
                        .filter(java.util.Objects::nonNull)
                        .toList());
        deleteByIds(
                "DELETE FROM ops_inbox_message WHERE id IN (%s)",
                candidates.stream().map(Candidate::inboxId).toList());
        return candidates.size();
    }

    private void deleteByIds(String sqlTemplate, List<Long> ids) {
        if (ids.isEmpty()) {
            return;
        }
        String placeholders = String.join(
                ",", java.util.Collections.nCopies(ids.size(), "?"));
        jdbc.update(
                sqlTemplate.formatted(placeholders),
                ids.toArray());
    }

    private static Long nullableLong(
            java.sql.ResultSet rs,
            String column) throws java.sql.SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private record Candidate(long inboxId, Long edgeId, Long taskId) {
    }
}
