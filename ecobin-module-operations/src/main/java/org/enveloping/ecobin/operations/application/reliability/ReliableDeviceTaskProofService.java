package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

@Service
public class ReliableDeviceTaskProofService
        implements ReliableDeviceTaskProofPort {

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;

    public ReliableDeviceTaskProofService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void completeDispatchFromTrustedCommandObservation(
            UUID commandUid) {
        Objects.requireNonNull(commandUid, "commandUid");
        List<LockedTask> rows = jdbc.query("""
                        SELECT task.id, task.state
                        FROM dev_device_command command_row
                        JOIN ops_reliable_task task
                          ON task.source_device_command_id = command_row.id
                        WHERE command_row.command_uid = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new LockedTask(
                        rs.getLong("id"),
                        rs.getString("state")),
                commandUid.toString());
        if (rows.size() != 1) {
            throw new ReliableTaskInvariantException(
                    "trusted command observation did not resolve one reliable task");
        }
        complete(rows.getFirst());
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void applyDeviceEntryUrlApplicationResult(
            UUID commandUid,
            String hardwareSn,
            String deviceEntryUrlSha256,
            String status,
            String faultCode) {
        Objects.requireNonNull(commandUid, "commandUid");
        Objects.requireNonNull(hardwareSn, "hardwareSn");
        Objects.requireNonNull(deviceEntryUrlSha256,
                "deviceEntryUrlSha256");
        if (!("APPLIED".equals(status) && faultCode == null)
                && !("FAILED".equals(status)
                && faultCode != null && !faultCode.isBlank())) {
            throw new ReliableTaskInvariantException(
                    "device entry URL result status is invalid");
        }
        List<PlatformCommandTask> rows = jdbc.query("""
                        SELECT task.id, task.state,
                               task.redacted_execution_snapshot,
                               task.blocked_reason_code,
                               task.blocked_diagnostic
                        FROM ops_reliable_task task
                        JOIN dev_device_asset asset
                          ON asset.id = task.source_device_asset_id
                        WHERE task.scope_kind = 'PLATFORM'
                          AND task.tenant_id IS NULL
                          AND task.organization_id IS NULL
                          AND task.task_type = 'SYNC_DEVICE_ENTRY_URL'
                          AND task.target_type = 'DEVICE_ASSET'
                          AND task.target_stable_key = ?
                          AND task.source_device_command_id IS NULL
                          AND asset.hardware_sn = ?
                          AND JSON_UNQUOTE(JSON_EXTRACT(
                              task.redacted_execution_snapshot,
                              '$.commandUid')) = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new PlatformCommandTask(
                        rs.getLong("id"),
                        rs.getString("state"),
                        rs.getString("redacted_execution_snapshot"),
                        rs.getString("blocked_reason_code"),
                        rs.getString("blocked_diagnostic")),
                hardwareSn,
                hardwareSn,
                commandUid.toString());
        if (rows.size() != 1) {
            throw new ReliableTaskInvariantException(
                    "device entry URL result did not resolve one platform task");
        }
        PlatformCommandTask task = rows.getFirst();
        JsonNode envelope = objectMapper.readTree(task.envelopeJson());
        if (!commandUid.toString().equals(
                envelope.path("commandUid").asText())
                || !"SYNC_DEVICE_ENTRY_URL".equals(
                envelope.path("commandType").asText())
                || !hardwareSn.equals(
                envelope.path("targetDeviceName").asText())
                || !"DEVICE_ASSET".equals(
                envelope.path("target").path("type").asText())
                || !hardwareSn.equals(
                envelope.path("target").path("uid").asText())
                || !deviceEntryUrlSha256.equals(
                envelope.path("payload")
                        .path("deviceEntryUrlSha256").asText())) {
            throw new ReliableTaskInvariantException(
                    "device entry URL result differs from the frozen command");
        }
        if ("CANCELLED".equals(task.state())) {
            return;
        }
        if ("DONE".equals(task.state())) {
            if ("APPLIED".equals(status)) {
                return;
            }
            throw new ReliableTaskInvariantException(
                    "device entry URL result conflicts with completed task");
        }
        if ("BLOCKED".equals(task.state())
                && "DEVICE_ENTRY_URL_APPLICATION_FAILED".equals(
                        task.blockedReasonCode())) {
            String expectedDiagnostic = failureDiagnostic(faultCode);
            if ("FAILED".equals(status)
                    && expectedDiagnostic.equals(
                            task.blockedDiagnostic())) {
                return;
            }
            throw new ReliableTaskInvariantException(
                    "device entry URL result conflicts with blocked task");
        }
        if ("APPLIED".equals(status)) {
            complete(new LockedTask(task.taskId(), task.state()));
            return;
        }
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        int updated = jdbc.update("""
                        UPDATE ops_reliable_task
                        SET state = 'BLOCKED',
                            next_run_at = NULL,
                            lease_token = NULL,
                            lease_worker = NULL,
                            lease_until = NULL,
                            dispatch_wait_reason = NULL,
                            handled_wake_version = wake_version,
                            completed_at = ?,
                            blocked_reason_code =
                                'DEVICE_ENTRY_URL_APPLICATION_FAILED',
                            blocked_diagnostic = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND state IN ('PENDING', 'BLOCKED')
                        """,
                now,
                failureDiagnostic(faultCode),
                now,
                task.taskId());
        if (updated != 1) {
            throw new ReliableTaskInvariantException(
                    "device entry URL failure could not block its task");
        }
    }

    private static String failureDiagnostic(String faultCode) {
        return "MCU/HMI URL application failed: " + faultCode;
    }

    private void complete(LockedTask task) {
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
                now,
                now,
                task.taskId());
        if (updated != 1) {
            throw new ReliableTaskInvariantException(
                    "trusted device proof could not complete its task");
        }
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

    private record PlatformCommandTask(
            long taskId,
            String state,
            String envelopeJson,
            String blockedReasonCode,
            String blockedDiagnostic) {
    }
}
