package org.enveloping.ecobin.operations.infrastructure.persistence.reliability;

import org.enveloping.ecobin.operations.application.reliability.ClaimedInboxTask;
import org.enveloping.ecobin.operations.application.reliability.ClaimedDeviceCommandTask;
import org.enveloping.ecobin.operations.application.reliability.ReliableTaskChannel;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.UUID;

@Repository
public class ReliableOperationsJdbcRepository {

    private final JdbcTemplate jdbcTemplate;

    public ReliableOperationsJdbcRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public LocalDateTime databaseNow() {
        return jdbcTemplate.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    public void insertInbox(NewInbox inbox, LocalDateTime now) {
        jdbcTemplate.update("""
                INSERT INTO ops_inbox_message (
                    inbox_uid, scope_kind, tenant_id, organization_id,
                    source_namespace, source_principal_key, external_message_id,
                    message_kind, normalized_schema_version,
                    raw_transport_body, raw_transport_sha256,
                    normalized_payload, normalized_content_sha256,
                    authentication_method, authentication_principal_ref,
                    correlation_uid, causation_uid, processing_state,
                    first_received_at, last_received_at, delivery_count,
                    processed_at, lock_version, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, CAST(? AS JSON), ?,
                    ?, ?, ?, ?, 'RECEIVED',
                    ?, ?, 1, NULL, 0, ?, ?
                )
                """,
                inbox.inboxUid().toString(),
                inbox.scopeKind(),
                inbox.tenantId(),
                inbox.organizationId(),
                inbox.sourceNamespace(),
                inbox.sourcePrincipalKey(),
                inbox.externalMessageId(),
                inbox.messageKind(),
                inbox.normalizedSchemaVersion(),
                inbox.rawTransportBody(),
                inbox.rawTransportSha256(),
                inbox.normalizedPayload(),
                inbox.normalizedContentSha256(),
                inbox.authenticationMethod(),
                inbox.authenticationPrincipalRef(),
                nullableUuid(inbox.correlationUid()),
                nullableUuid(inbox.causationUid()),
                now,
                now,
                now,
                now);
    }

    public Optional<InboxAggregate> lockInboxByExternalIdentity(
            String sourceNamespace,
            String sourcePrincipalKey,
            String externalMessageId) {
        List<InboxCore> rows = jdbcTemplate.query("""
                SELECT
                    id,
                    inbox_uid,
                    scope_kind,
                    tenant_id,
                    organization_id,
                    message_kind,
                    normalized_schema_version,
                    CAST(normalized_payload AS CHAR) AS normalized_payload,
                    LOWER(HEX(normalized_content_sha256)) AS content_sha256,
                    processing_state
                FROM ops_inbox_message
                WHERE source_namespace = ?
                  AND source_principal_key = ?
                  AND external_message_id = ?
                FOR UPDATE
                """,
                (resultSet, rowNumber) -> new InboxCore(
                        resultSet.getLong("id"),
                        UUID.fromString(resultSet.getString("inbox_uid")),
                        resultSet.getString("scope_kind"),
                        nullableLong(resultSet, "tenant_id"),
                        nullableLong(resultSet, "organization_id"),
                        resultSet.getString("message_kind"),
                        resultSet.getInt("normalized_schema_version"),
                        resultSet.getString("normalized_payload"),
                        resultSet.getString("content_sha256"),
                        resultSet.getString("processing_state")),
                sourceNamespace,
                sourcePrincipalKey,
                externalMessageId);
        return rows.stream().findFirst().map(this::lockInboxAggregate);
    }

    public InboxAggregate lockInboxByUid(UUID inboxUid) {
        InboxCore inbox = jdbcTemplate.queryForObject("""
                SELECT
                    id,
                    inbox_uid,
                    scope_kind,
                    tenant_id,
                    organization_id,
                    message_kind,
                    normalized_schema_version,
                    CAST(normalized_payload AS CHAR) AS normalized_payload,
                    LOWER(HEX(normalized_content_sha256)) AS content_sha256,
                    processing_state
                FROM ops_inbox_message
                WHERE inbox_uid = ?
                FOR UPDATE
                """,
                (resultSet, rowNumber) -> new InboxCore(
                        resultSet.getLong("id"),
                        UUID.fromString(resultSet.getString("inbox_uid")),
                        resultSet.getString("scope_kind"),
                        nullableLong(resultSet, "tenant_id"),
                        nullableLong(resultSet, "organization_id"),
                        resultSet.getString("message_kind"),
                        resultSet.getInt("normalized_schema_version"),
                        resultSet.getString("normalized_payload"),
                        resultSet.getString("content_sha256"),
                        resultSet.getString("processing_state")),
                inboxUid.toString());
        return lockInboxAggregate(inbox);
    }

    private InboxAggregate lockInboxAggregate(InboxCore inbox) {
        List<TaskLink> tasks = jdbcTemplate.query("""
                SELECT
                    id,
                    task_uid,
                    execution_lane,
                    LOWER(HEX(payload_sha256)) AS task_payload_sha256
                FROM ops_reliable_task
                WHERE source_inbox_id = ?
                FOR UPDATE
                """,
                (resultSet, rowNumber) -> new TaskLink(
                        resultSet.getLong("id"),
                        UUID.fromString(resultSet.getString("task_uid")),
                        resultSet.getString("execution_lane"),
                        resultSet.getString("task_payload_sha256")),
                inbox.inboxId());
        TaskLink task = tasks.stream().findFirst().orElse(null);
        return new InboxAggregate(
                inbox.inboxId(),
                inbox.inboxUid(),
                inbox.scopeKind(),
                inbox.tenantId(),
                inbox.organizationId(),
                inbox.messageKind(),
                inbox.normalizedSchemaVersion(),
                inbox.normalizedPayload(),
                inbox.normalizedContentSha256Hex(),
                inbox.processingState(),
                task == null ? null : task.taskId(),
                task == null ? null : task.taskUid(),
                task == null ? null : task.executionLane(),
                task == null ? null : task.payloadSha256Hex());
    }

    public void touchDuplicate(long inboxId, LocalDateTime now) {
        int updated = jdbcTemplate.update("""
                UPDATE ops_inbox_message
                SET last_received_at = ?,
                    delivery_count = delivery_count + 1,
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                """, now, now, inboxId);
        requireSingleRow(updated, "touch duplicate inbox");
    }

    public UUID insertProcessInboxTask(
            long inboxId,
            UUID inboxUid,
            String scopeKind,
            Long tenantId,
            Long organizationId,
            String executionLane,
            String redactedExecutionSnapshot,
            byte[] payloadSha256,
            UUID correlationUid,
            UUID causationUid,
            int maxAutoAttempts,
            LocalDateTime now) {
        UUID taskUid = UUID.randomUUID();
        String taskKey = "PROCESS_INBOX:"
                + inboxUid.toString().toUpperCase(Locale.ROOT);
        jdbcTemplate.update("""
                INSERT INTO ops_reliable_task (
                    task_uid, scope_kind, tenant_id, organization_id,
                    task_category, task_type, execution_lane, task_key,
                    target_type, target_stable_key,
                    source_inbox_id, source_device_deployment_id,
                    source_device_command_id, payload_schema_version,
                    redacted_execution_snapshot, payload_sha256,
                    correlation_uid, causation_uid, initiating_audit_id,
                    priority, retry_policy_version, max_auto_attempts,
                    state, next_run_at, lease_token, lease_worker, lease_until,
                    attempt_sequence, consecutive_failure_count, wake_version,
                    handled_wake_version, completed_at, blocked_reason_code,
                    blocked_diagnostic, lock_version, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?,
                    'INBOX_PROCESSING', 'PROCESS_INBOX', ?, ?,
                    'INBOX_MESSAGE', ?,
                    ?, NULL, NULL, 1,
                    CAST(? AS JSON), ?,
                    ?, ?, NULL,
                    100, 1, ?,
                    'PENDING', ?, NULL, NULL, NULL,
                    0, 0, 0, 0, NULL, NULL, NULL, 0, ?, ?
                )
                """,
                taskUid.toString(),
                scopeKind,
                tenantId,
                organizationId,
                executionLane,
                taskKey,
                inboxUid.toString(),
                inboxId,
                redactedExecutionSnapshot,
                payloadSha256,
                nullableUuid(correlationUid),
                nullableUuid(causationUid),
                maxAutoAttempts,
                now,
                now,
                now);
        return taskUid;
    }

    public UUID insertDeviceBusinessTask(
            long tenantId,
            long organizationId,
            long deploymentId,
            long commandId,
            String taskType,
            String taskKey,
            String targetType,
            String targetStableKey,
            int payloadSchemaVersion,
            String redactedExecutionSnapshot,
            byte[] payloadSha256,
            UUID correlationUid,
            UUID causationUid,
            int maxAutoAttempts,
            LocalDateTime now) {
        UUID taskUid = UUID.randomUUID();
        int inserted = jdbcTemplate.update("""
                INSERT INTO ops_reliable_task (
                    task_uid, scope_kind, tenant_id, organization_id,
                    task_category, task_type, execution_lane, task_key,
                    target_type, target_stable_key,
                    source_inbox_id, source_device_deployment_id,
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
                    'BUSINESS_INTENT', ?, 'DEVICE', ?,
                    ?, ?,
                    NULL, ?, ?, ?,
                    CAST(? AS JSON), ?,
                    ?, ?, NULL,
                    100, 1, ?,
                    'PENDING', ?, NULL, NULL, NULL,
                    0, 0, 0, 0, NULL, NULL, NULL, 0, ?, ?
                )
                """,
                taskUid.toString(),
                tenantId,
                organizationId,
                taskType,
                taskKey,
                targetType,
                targetStableKey,
                deploymentId,
                commandId,
                payloadSchemaVersion,
                redactedExecutionSnapshot,
                payloadSha256,
                nullableUuid(correlationUid),
                nullableUuid(causationUid),
                maxAutoAttempts,
                now,
                now,
                now);
        requireSingleRow(inserted, "insert device business task");
        return taskUid;
    }

    public void cancelSupersededDeviceTasks(
            long tenantId,
            long organizationId,
            long deploymentId,
            String taskType,
            LocalDateTime now) {
        jdbcTemplate.update("""
                UPDATE ops_reliable_task
                SET state = 'CANCELLED',
                    next_run_at = NULL,
                    lease_token = NULL,
                    lease_worker = NULL,
                    lease_until = NULL,
                    handled_wake_version = wake_version,
                    completed_at = COALESCE(completed_at, ?),
                    blocked_reason_code = NULL,
                    blocked_diagnostic = NULL,
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE scope_kind = 'ORGANIZATION'
                  AND tenant_id = ?
                  AND organization_id = ?
                  AND source_device_deployment_id = ?
                  AND task_type = ?
                  AND state IN ('PENDING', 'BLOCKED')
                  AND lease_token IS NULL
                """,
                now,
                now,
                tenantId,
                organizationId,
                deploymentId,
                taskType);
    }

    public UUID upsertIdentityConflict(
            byte[] dedupeKey,
            String scopeKind,
            Long tenantId,
            Long organizationId,
            String sourceNamespace,
            String sourcePrincipalKey,
            String externalMessageId,
            long conflictingInboxId,
            byte[] rawTransportSha256,
            byte[] normalizedContentSha256,
            LocalDateTime now) {
        UUID proposedUid = UUID.randomUUID();
        jdbcTemplate.update("""
                INSERT INTO ops_message_quarantine (
                    quarantine_uid, dedupe_key, scope_kind, tenant_id,
                    organization_id, source_namespace, source_principal_key,
                    external_message_id, conflicting_inbox_id, reason_code,
                    raw_transport_sha256, normalized_content_sha256,
                    redacted_diagnostic_payload, status,
                    first_seen_at, last_seen_at, discovery_count,
                    acknowledged_audit_id, acknowledged_at, lock_version,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, 'IDENTITY_CONTENT_CONFLICT',
                    ?, ?,
                    'stable external identity carried different semantic content',
                    'OPEN', ?, ?, 1, NULL, NULL, 0, ?, ?
                )
                ON DUPLICATE KEY UPDATE
                    last_seen_at = VALUES(last_seen_at),
                    discovery_count = discovery_count + 1,
                    lock_version = lock_version + 1,
                    updated_at = VALUES(updated_at)
                """,
                proposedUid.toString(),
                dedupeKey,
                scopeKind,
                tenantId,
                organizationId,
                sourceNamespace,
                sourcePrincipalKey,
                externalMessageId,
                conflictingInboxId,
                rawTransportSha256,
                normalizedContentSha256,
                now,
                now,
                now,
                now);
        String storedUid = jdbcTemplate.queryForObject("""
                SELECT quarantine_uid
                FROM ops_message_quarantine
                WHERE dedupe_key = ?
                """, String.class, dedupeKey);
        return UUID.fromString(storedUid);
    }

    public List<ClaimedInboxTask> claimInboxTasks(
            ReliableTaskChannel channel,
            String workerId,
            int batchSize,
            Duration leaseDuration) {
        if (channel == ReliableTaskChannel.MAINTENANCE) {
            return List.of();
        }
        LocalDateTime now = databaseNow();
        String channelPredicate = switch (channel) {
            case IOT_DEVICE -> "t.execution_lane = 'DEVICE'";
            case FUNDS_WECHAT -> "t.execution_lane = 'FUNDS'";
            case MAINTENANCE -> throw new IllegalStateException(
                    "maintenance does not claim PROCESS_INBOX tasks");
        };
        String sql = """
                SELECT
                    t.id AS task_id,
                    t.task_uid,
                    t.lease_token AS previous_lease_token,
                    t.attempt_sequence,
                    t.wake_version,
                    t.source_inbox_id
                FROM ops_reliable_task t FORCE INDEX (ix_ops_task_claim)
                WHERE t.state = 'PENDING'
                  AND t.task_type = 'PROCESS_INBOX'
                  AND t.claimable_at <= UTC_TIMESTAMP(3)
                  AND (
                """ + channelPredicate + """
                  )
                ORDER BY t.claimable_at, t.priority, t.id
                LIMIT ?
                FOR UPDATE SKIP LOCKED
                """;
        List<ClaimCandidate> candidates = jdbcTemplate.query(
                connection -> {
                    var statement = connection.prepareStatement(sql);
                    statement.setInt(1, batchSize);
                    return statement;
                },
                (resultSet, rowNumber) -> new ClaimCandidate(
                        resultSet.getLong("task_id"),
                        UUID.fromString(resultSet.getString("task_uid")),
                        nullableUuid(resultSet.getString("previous_lease_token")),
                        resultSet.getLong("attempt_sequence"),
                        resultSet.getLong("wake_version"),
                        resultSet.getLong("source_inbox_id")));
        LocalDateTime leaseUntil = now.plus(leaseDuration);
        return candidates.stream()
                .map(candidate -> claim(candidate, workerId, now, leaseUntil))
                .toList();
    }

    private ClaimedInboxTask claim(
            ClaimCandidate candidate,
            String workerId,
            LocalDateTime now,
            LocalDateTime leaseUntil) {
        InboxPayload inbox = jdbcTemplate.queryForObject("""
                SELECT
                    inbox_uid,
                    scope_kind,
                    tenant_id,
                    organization_id,
                    message_kind,
                    normalized_schema_version,
                    CAST(normalized_payload AS CHAR) AS normalized_payload
                FROM ops_inbox_message
                WHERE id = ?
                """,
                (resultSet, rowNumber) -> new InboxPayload(
                        UUID.fromString(resultSet.getString("inbox_uid")),
                        resultSet.getString("scope_kind"),
                        nullableLong(resultSet, "tenant_id"),
                        nullableLong(resultSet, "organization_id"),
                        resultSet.getString("message_kind"),
                        resultSet.getInt("normalized_schema_version"),
                        resultSet.getString("normalized_payload")),
                candidate.inboxId());
        if (candidate.previousLeaseToken() != null) {
            jdbcTemplate.update("""
                    UPDATE ops_task_attempt
                    SET reclaimed_at = ?
                    WHERE lease_token = ?
                      AND reclaimed_at IS NULL
                    """,
                    now,
                    candidate.previousLeaseToken().toString());
        }
        long attemptNo = candidate.attemptSequence() + 1;
        UUID attemptUid = UUID.randomUUID();
        UUID leaseToken = UUID.randomUUID();
        int updated = jdbcTemplate.update("""
                UPDATE ops_reliable_task
                SET lease_token = ?,
                    lease_worker = ?,
                    lease_until = ?,
                    attempt_sequence = ?,
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                  AND state = 'PENDING'
                """,
                leaseToken.toString(),
                workerId,
                leaseUntil,
                attemptNo,
                now,
                candidate.taskId());
        requireSingleRow(updated, "claim reliable task");
        jdbcTemplate.update("""
                INSERT INTO ops_task_attempt (
                    attempt_uid, task_id, scope_kind, tenant_id,
                    organization_id, attempt_no, lease_token,
                    claimed_wake_version, worker_id, claimed_at, lease_until,
                    external_call_may_have_started_at, reclaimed_at,
                    result_recorded_at, action_kind, technical_result,
                    request_sha256, response_sha256, http_status,
                    external_api_error_code, duration_ms, redacted_diagnostic,
                    created_at
                ) VALUES (
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    NULL, NULL,
                    NULL, 'PROCESS', NULL,
                    NULL, NULL, NULL,
                    NULL, NULL, NULL, ?
                )
                """,
                attemptUid.toString(),
                candidate.taskId(),
                inbox.scopeKind(),
                inbox.tenantId(),
                inbox.organizationId(),
                attemptNo,
                leaseToken.toString(),
                candidate.wakeVersion(),
                workerId,
                now,
                leaseUntil,
                now);
        return new ClaimedInboxTask(
                candidate.taskUid(),
                inbox.inboxUid(),
                candidate.inboxId(),
                inbox.scopeKind(),
                inbox.tenantId(),
                inbox.organizationId(),
                attemptUid,
                leaseToken,
                candidate.wakeVersion(),
                inbox.messageKind(),
                inbox.normalizedSchemaVersion(),
                inbox.normalizedPayload(),
                now,
                leaseUntil);
    }

    public List<ClaimedDeviceCommandTask> claimDeviceCommandTasks(
            String workerId,
            int batchSize,
            Duration leaseDuration) {
        LocalDateTime now = databaseNow();
        String sql = """
                SELECT
                    t.id AS task_id,
                    t.task_uid,
                    t.tenant_id,
                    t.organization_id,
                    t.lease_token AS previous_lease_token,
                    t.attempt_sequence,
                    t.wake_version,
                    c.command_uid,
                    c.command_type,
                    CAST(c.semantic_payload AS CHAR) AS semantic_payload,
                    c.semantic_payload_sha256,
                    a.hardware_sn
                FROM ops_reliable_task t FORCE INDEX (ix_ops_task_claim)
                JOIN dev_device_command c
                  ON c.tenant_id = t.tenant_id
                 AND c.organization_id = t.organization_id
                 AND c.deployment_id = t.source_device_deployment_id
                 AND c.id = t.source_device_command_id
                JOIN dev_device_deployment d
                  ON d.tenant_id = c.tenant_id
                 AND d.organization_id = c.organization_id
                 AND d.id = c.deployment_id
                JOIN dev_device_asset a ON a.id = d.asset_id
                WHERE t.state = 'PENDING'
                  AND t.task_category = 'BUSINESS_INTENT'
                  AND t.task_type = 'ENSURE_DEVICE_CONFIGURATION'
                  AND t.execution_lane = 'DEVICE'
                  AND t.claimable_at <= UTC_TIMESTAMP(3)
                ORDER BY t.claimable_at, t.priority, t.id
                LIMIT ?
                FOR UPDATE SKIP LOCKED
                """;
        List<DeviceClaimCandidate> candidates = jdbcTemplate.query(
                connection -> {
                    var statement = connection.prepareStatement(sql);
                    statement.setInt(1, batchSize);
                    return statement;
                },
                (resultSet, rowNumber) -> new DeviceClaimCandidate(
                        resultSet.getLong("task_id"),
                        UUID.fromString(resultSet.getString("task_uid")),
                        resultSet.getLong("tenant_id"),
                        resultSet.getLong("organization_id"),
                        nullableUuid(
                                resultSet.getString(
                                        "previous_lease_token")),
                        resultSet.getLong("attempt_sequence"),
                        resultSet.getLong("wake_version"),
                        UUID.fromString(
                                resultSet.getString("command_uid")),
                        resultSet.getString("command_type"),
                        resultSet.getString("hardware_sn"),
                        resultSet.getString("semantic_payload"),
                        resultSet.getBytes("semantic_payload_sha256")));
        LocalDateTime leaseUntil = now.plus(leaseDuration);
        return candidates.stream()
                .map(candidate -> claimDeviceCommand(
                        candidate, workerId, now, leaseUntil))
                .toList();
    }

    private ClaimedDeviceCommandTask claimDeviceCommand(
            DeviceClaimCandidate candidate,
            String workerId,
            LocalDateTime now,
            LocalDateTime leaseUntil) {
        if (candidate.previousLeaseToken() != null) {
            jdbcTemplate.update("""
                    UPDATE ops_task_attempt
                    SET reclaimed_at = ?
                    WHERE lease_token = ?
                      AND reclaimed_at IS NULL
                    """,
                    now,
                    candidate.previousLeaseToken().toString());
        }
        long attemptNo = candidate.attemptSequence() + 1;
        UUID attemptUid = UUID.randomUUID();
        UUID leaseToken = UUID.randomUUID();
        int updated = jdbcTemplate.update("""
                UPDATE ops_reliable_task
                SET lease_token = ?,
                    lease_worker = ?,
                    lease_until = ?,
                    attempt_sequence = ?,
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                  AND state = 'PENDING'
                """,
                leaseToken.toString(),
                workerId,
                leaseUntil,
                attemptNo,
                now,
                candidate.taskId());
        requireSingleRow(updated, "claim device command task");
        jdbcTemplate.update("""
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
                    ?, ?, 'ORGANIZATION', ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    NULL, NULL,
                    NULL, 'SUBMIT', NULL,
                    NULL, NULL, NULL,
                    NULL, NULL, NULL, ?
                )
                """,
                attemptUid.toString(),
                candidate.taskId(),
                candidate.tenantId(),
                candidate.organizationId(),
                attemptNo,
                leaseToken.toString(),
                candidate.wakeVersion(),
                workerId,
                now,
                leaseUntil,
                now);
        return new ClaimedDeviceCommandTask(
                candidate.taskUid(),
                candidate.commandUid(),
                attemptUid,
                leaseToken,
                candidate.wakeVersion(),
                candidate.commandType(),
                candidate.hardwareSn(),
                candidate.semanticEnvelopeJson(),
                candidate.semanticEnvelopeSha256(),
                now,
                leaseUntil);
    }

    public void markExternalCallMayHaveStarted(
            UUID attemptUid, UUID leaseToken, LocalDateTime now) {
        int updated = jdbcTemplate.update("""
                UPDATE ops_task_attempt
                SET external_call_may_have_started_at = ?
                WHERE attempt_uid = ?
                  AND lease_token = ?
                  AND external_call_may_have_started_at IS NULL
                  AND result_recorded_at IS NULL
                """,
                now,
                attemptUid.toString(),
                leaseToken.toString());
        requireSingleRow(updated, "mark external device call started");
    }

    public DeviceTaskExecution lockDeviceTaskExecution(
            UUID taskUid, UUID commandUid, UUID attemptUid) {
        LockedDeviceTask task = jdbcTemplate.queryForObject("""
                SELECT
                    t.id,
                    t.state,
                    t.lease_token,
                    t.lease_until,
                    t.wake_version,
                    t.handled_wake_version,
                    t.consecutive_failure_count,
                    t.max_auto_attempts,
                    (
                        SELECT COUNT(*)
                        FROM ops_task_attempt counted
                        WHERE counted.task_id = t.id
                          AND counted.claimed_wake_version = t.wake_version
                    ) AS attempts_for_current_wake
                FROM ops_reliable_task t
                JOIN dev_device_command c
                  ON c.id = t.source_device_command_id
                 AND c.command_uid = ?
                WHERE t.task_uid = ?
                  AND t.task_category = 'BUSINESS_INTENT'
                  AND t.execution_lane = 'DEVICE'
                FOR UPDATE
                """,
                (resultSet, rowNumber) -> new LockedDeviceTask(
                        resultSet.getLong("id"),
                        resultSet.getString("state"),
                        nullableUuid(resultSet.getString("lease_token")),
                        resultSet.getObject(
                                "lease_until", LocalDateTime.class),
                        resultSet.getLong("wake_version"),
                        resultSet.getLong("handled_wake_version"),
                        resultSet.getInt("consecutive_failure_count"),
                        resultSet.getInt("max_auto_attempts"),
                        resultSet.getInt("attempts_for_current_wake")),
                commandUid.toString(),
                taskUid.toString());
        LockedAttempt attempt = jdbcTemplate.queryForObject("""
                SELECT
                    id,
                    lease_token,
                    claimed_wake_version,
                    technical_result
                FROM ops_task_attempt
                WHERE task_id = ?
                  AND attempt_uid = ?
                FOR UPDATE
                """,
                (resultSet, rowNumber) -> new LockedAttempt(
                        resultSet.getLong("id"),
                        UUID.fromString(
                                resultSet.getString("lease_token")),
                        resultSet.getLong("claimed_wake_version"),
                        resultSet.getString("technical_result")),
                task.taskId(),
                attemptUid.toString());
        return new DeviceTaskExecution(
                task.taskId(),
                task.state(),
                task.currentLeaseToken(),
                task.leaseUntil(),
                task.wakeVersion(),
                task.handledWakeVersion(),
                task.consecutiveFailureCount(),
                task.maxAutoAttempts(),
                task.attemptsForCurrentWake(),
                attempt.attemptId(),
                attempt.leaseToken(),
                attempt.claimedWakeVersion(),
                attempt.technicalResult());
    }

    public void recordDeviceAttemptResult(
            long attemptId,
            String technicalResult,
            long durationMillis,
            byte[] requestSha256,
            byte[] responseSha256,
            Integer httpStatus,
            String externalApiErrorCode,
            String redactedDiagnostic,
            LocalDateTime now) {
        int updated = jdbcTemplate.update("""
                UPDATE ops_task_attempt
                SET result_recorded_at = ?,
                    technical_result = ?,
                    request_sha256 = ?,
                    response_sha256 = ?,
                    http_status = ?,
                    external_api_error_code = ?,
                    duration_ms = ?,
                    redacted_diagnostic = ?
                WHERE id = ?
                  AND result_recorded_at IS NULL
                """,
                now,
                technicalResult,
                requestSha256,
                responseSha256,
                httpStatus,
                externalApiErrorCode,
                Math.max(0, durationMillis),
                redactedDiagnostic,
                attemptId);
        if (updated != 0 && updated != 1) {
            throw new IllegalStateException(
                    "record device attempt result updated an unexpected row count");
        }
    }

    public void scheduleAwaitingDeviceEvidence(
            long taskId,
            LocalDateTime nextRunAt,
            LocalDateTime now) {
        int updated = jdbcTemplate.update("""
                UPDATE ops_reliable_task
                SET state = 'PENDING',
                    next_run_at = ?,
                    lease_token = NULL,
                    lease_worker = NULL,
                    lease_until = NULL,
                    consecutive_failure_count = 0,
                    completed_at = NULL,
                    blocked_reason_code = NULL,
                    blocked_diagnostic = NULL,
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                nextRunAt,
                now,
                taskId);
        requireSingleRow(updated, "schedule device evidence recheck");
    }

    public void blockDeviceTask(
            long taskId,
            int failureCount,
            long handledWakeVersion,
            String reasonCode,
            String diagnostic,
            LocalDateTime now) {
        int updated = jdbcTemplate.update("""
                UPDATE ops_reliable_task
                SET state = 'BLOCKED',
                    next_run_at = NULL,
                    lease_token = NULL,
                    lease_worker = NULL,
                    lease_until = NULL,
                    consecutive_failure_count = ?,
                    handled_wake_version = ?,
                    completed_at = ?,
                    blocked_reason_code = ?,
                    blocked_diagnostic = ?,
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                failureCount,
                handledWakeVersion,
                now,
                reasonCode,
                diagnostic,
                now,
                taskId);
        requireSingleRow(updated, "block device task");
    }

    public TaskExecution lockTaskExecution(
            UUID taskUid, UUID inboxUid, UUID attemptUid) {
        LockedInbox inbox = jdbcTemplate.queryForObject("""
                SELECT id, processing_state
                FROM ops_inbox_message
                WHERE inbox_uid = ?
                FOR UPDATE
                """,
                (resultSet, rowNumber) -> new LockedInbox(
                        resultSet.getLong("id"),
                        resultSet.getString("processing_state")),
                inboxUid.toString());
        LockedTask task = jdbcTemplate.queryForObject("""
                SELECT
                    id,
                    state,
                    lease_token,
                    lease_until,
                    wake_version,
                    handled_wake_version,
                    consecutive_failure_count,
                    max_auto_attempts
                FROM ops_reliable_task
                WHERE task_uid = ?
                  AND source_inbox_id = ?
                FOR UPDATE
                """,
                (resultSet, rowNumber) -> new LockedTask(
                        resultSet.getLong("id"),
                        resultSet.getString("state"),
                        nullableUuid(resultSet.getString("lease_token")),
                        resultSet.getObject("lease_until", LocalDateTime.class),
                        resultSet.getLong("wake_version"),
                        resultSet.getLong("handled_wake_version"),
                        resultSet.getInt("consecutive_failure_count"),
                        resultSet.getInt("max_auto_attempts")),
                taskUid.toString(),
                inbox.inboxId());
        LockedAttempt attempt = jdbcTemplate.queryForObject("""
                SELECT
                    id,
                    lease_token,
                    claimed_wake_version,
                    technical_result
                FROM ops_task_attempt
                WHERE task_id = ?
                  AND attempt_uid = ?
                FOR UPDATE
                """,
                (resultSet, rowNumber) -> new LockedAttempt(
                        resultSet.getLong("id"),
                        UUID.fromString(resultSet.getString("lease_token")),
                        resultSet.getLong("claimed_wake_version"),
                        resultSet.getString("technical_result")),
                task.taskId(),
                attemptUid.toString());
        return new TaskExecution(
                task.taskId(),
                task.state(),
                task.currentLeaseToken(),
                task.leaseUntil(),
                task.wakeVersion(),
                task.handledWakeVersion(),
                task.consecutiveFailureCount(),
                task.maxAutoAttempts(),
                inbox.inboxId(),
                inbox.processingState(),
                attempt.attemptId(),
                attempt.leaseToken(),
                attempt.claimedWakeVersion(),
                attempt.technicalResult());
    }

    public void recordAttemptResult(
            long attemptId,
            String technicalResult,
            long durationMillis,
            String redactedDiagnostic,
            LocalDateTime now) {
        int updated = jdbcTemplate.update("""
                UPDATE ops_task_attempt
                SET result_recorded_at = ?,
                    technical_result = ?,
                    duration_ms = ?,
                    redacted_diagnostic = ?
                WHERE id = ?
                  AND result_recorded_at IS NULL
                """,
                now,
                technicalResult,
                durationMillis,
                redactedDiagnostic,
                attemptId);
        if (updated != 0 && updated != 1) {
            throw new IllegalStateException(
                    "record attempt result updated an unexpected row count");
        }
    }

    public void markInboxProcessed(long inboxId, LocalDateTime now) {
        int updated = jdbcTemplate.update("""
                UPDATE ops_inbox_message
                SET processing_state = 'PROCESSED',
                    processed_at = COALESCE(processed_at, ?),
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                  AND processing_state IN ('RECEIVED', 'PROCESSED')
                """, now, now, inboxId);
        requireSingleRow(updated, "mark inbox processed");
    }

    public void markTaskDone(
            long taskId, long handledWakeVersion, LocalDateTime now) {
        int updated = jdbcTemplate.update("""
                UPDATE ops_reliable_task
                SET state = 'DONE',
                    next_run_at = NULL,
                    lease_token = NULL,
                    lease_worker = NULL,
                    lease_until = NULL,
                    consecutive_failure_count = 0,
                    handled_wake_version = ?,
                    completed_at = ?,
                    blocked_reason_code = NULL,
                    blocked_diagnostic = NULL,
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                """, handledWakeVersion, now, now, taskId);
        requireSingleRow(updated, "complete reliable task");
    }

    public void releaseForImmediateRecheck(long taskId, LocalDateTime now) {
        int updated = jdbcTemplate.update("""
                UPDATE ops_reliable_task
                SET state = 'PENDING',
                    next_run_at = ?,
                    lease_token = NULL,
                    lease_worker = NULL,
                    lease_until = NULL,
                    completed_at = NULL,
                    blocked_reason_code = NULL,
                    blocked_diagnostic = NULL,
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                """, now, now, taskId);
        requireSingleRow(updated, "release task for immediate recheck");
    }

    public void scheduleRetry(
            long taskId,
            int failureCount,
            LocalDateTime nextRunAt,
            LocalDateTime now) {
        int updated = jdbcTemplate.update("""
                UPDATE ops_reliable_task
                SET state = 'PENDING',
                    next_run_at = ?,
                    lease_token = NULL,
                    lease_worker = NULL,
                    lease_until = NULL,
                    consecutive_failure_count = ?,
                    completed_at = NULL,
                    blocked_reason_code = NULL,
                    blocked_diagnostic = NULL,
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                nextRunAt,
                failureCount,
                now,
                taskId);
        requireSingleRow(updated, "schedule reliable task retry");
    }

    public void blockAfterRetryExhaustion(
            long taskId,
            int failureCount,
            long handledWakeVersion,
            LocalDateTime now) {
        int updated = jdbcTemplate.update("""
                UPDATE ops_reliable_task
                SET state = 'BLOCKED',
                    next_run_at = NULL,
                    lease_token = NULL,
                    lease_worker = NULL,
                    lease_until = NULL,
                    consecutive_failure_count = ?,
                    handled_wake_version = ?,
                    completed_at = ?,
                    blocked_reason_code = 'AUTO_RETRY_EXHAUSTED',
                    blocked_diagnostic =
                        'automatic retry limit reached; inspect original task',
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                failureCount,
                handledWakeVersion,
                now,
                now,
                taskId);
        requireSingleRow(updated, "block reliable task");
    }

    public long wakeTask(UUID taskUid, LocalDateTime now) {
        WakeableTask task = jdbcTemplate.queryForObject("""
                SELECT id, state, lease_token, lease_until, wake_version
                FROM ops_reliable_task
                WHERE task_uid = ?
                FOR UPDATE
                """,
                (resultSet, rowNumber) -> new WakeableTask(
                        resultSet.getLong("id"),
                        resultSet.getString("state"),
                        nullableUuid(resultSet.getString("lease_token")),
                        resultSet.getObject("lease_until", LocalDateTime.class),
                        resultSet.getLong("wake_version")),
                taskUid.toString());
        long newVersion = task.wakeVersion() + 1;
        boolean hasLease = task.leaseToken() != null;
        if (!hasLease) {
            int updated = jdbcTemplate.update("""
                    UPDATE ops_reliable_task
                    SET state = 'PENDING',
                        next_run_at = ?,
                        wake_version = ?,
                        consecutive_failure_count = 0,
                        completed_at = NULL,
                        blocked_reason_code = NULL,
                        blocked_diagnostic = NULL,
                        lock_version = lock_version + 1,
                        updated_at = ?
                    WHERE id = ?
                    """, now, newVersion, now, task.id());
            requireSingleRow(updated, "wake static reliable task");
        } else {
            int updated = jdbcTemplate.update("""
                    UPDATE ops_reliable_task
                    SET wake_version = ?,
                        lock_version = lock_version + 1,
                        updated_at = ?
                    WHERE id = ?
                    """, newVersion, now, task.id());
            requireSingleRow(updated, "wake leased reliable task");
        }
        return newVersion;
    }

    public void incrementWakeForLateResult(long taskId, LocalDateTime now) {
        int updated = jdbcTemplate.update("""
                UPDATE ops_reliable_task
                SET wake_version = wake_version + 1,
                    state = CASE
                        WHEN lease_token IS NULL THEN 'PENDING'
                        ELSE state
                    END,
                    next_run_at = CASE
                        WHEN lease_token IS NULL THEN ?
                        ELSE next_run_at
                    END,
                    completed_at = CASE
                        WHEN lease_token IS NULL THEN NULL
                        ELSE completed_at
                    END,
                    blocked_reason_code = CASE
                        WHEN lease_token IS NULL THEN NULL
                        ELSE blocked_reason_code
                    END,
                    blocked_diagnostic = CASE
                        WHEN lease_token IS NULL THEN NULL
                        ELSE blocked_diagnostic
                    END,
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                """, now, now, taskId);
        requireSingleRow(updated, "wake task for late result");
    }

    private static void requireSingleRow(int updated, String operation) {
        if (updated != 1) {
            throw new IllegalStateException(
                    operation + " updated " + updated + " rows");
        }
    }

    private static String nullableUuid(UUID value) {
        return value == null ? null : value.toString();
    }

    private static UUID nullableUuid(String value) {
        return value == null ? null : UUID.fromString(value);
    }

    private static Long nullableLong(
            java.sql.ResultSet resultSet,
            String column) throws java.sql.SQLException {
        long value = resultSet.getLong(column);
        return resultSet.wasNull() ? null : value;
    }

    public record NewInbox(
            UUID inboxUid,
            String scopeKind,
            Long tenantId,
            Long organizationId,
            String sourceNamespace,
            String sourcePrincipalKey,
            String externalMessageId,
            String messageKind,
            int normalizedSchemaVersion,
            byte[] rawTransportBody,
            byte[] rawTransportSha256,
            String normalizedPayload,
            byte[] normalizedContentSha256,
            String authenticationMethod,
            String authenticationPrincipalRef,
            UUID correlationUid,
            UUID causationUid) {
    }

    public record InboxAggregate(
            long inboxId,
            UUID inboxUid,
            String scopeKind,
            Long tenantId,
            Long organizationId,
            String messageKind,
            int normalizedSchemaVersion,
            String normalizedPayload,
            String normalizedContentSha256Hex,
            String processingState,
            Long taskId,
            UUID taskUid,
            String executionLane,
            String taskPayloadSha256Hex) {
    }

    private record ClaimCandidate(
            long taskId,
            UUID taskUid,
            UUID previousLeaseToken,
            long attemptSequence,
            long wakeVersion,
            long inboxId) {
    }

    private record DeviceClaimCandidate(
            long taskId,
            UUID taskUid,
            long tenantId,
            long organizationId,
            UUID previousLeaseToken,
            long attemptSequence,
            long wakeVersion,
            UUID commandUid,
            String commandType,
            String hardwareSn,
            String semanticEnvelopeJson,
            byte[] semanticEnvelopeSha256) {
    }

    private record InboxPayload(
            UUID inboxUid,
            String scopeKind,
            Long tenantId,
            Long organizationId,
            String messageKind,
            int normalizedSchemaVersion,
            String normalizedPayload) {
    }

    private record InboxCore(
            long inboxId,
            UUID inboxUid,
            String scopeKind,
            Long tenantId,
            Long organizationId,
            String messageKind,
            int normalizedSchemaVersion,
            String normalizedPayload,
            String normalizedContentSha256Hex,
            String processingState) {
    }

    private record TaskLink(
            long taskId,
            UUID taskUid,
            String executionLane,
            String payloadSha256Hex) {
    }

    private record LockedInbox(long inboxId, String processingState) {
    }

    private record LockedTask(
            long taskId,
            String state,
            UUID currentLeaseToken,
            LocalDateTime leaseUntil,
            long wakeVersion,
            long handledWakeVersion,
            int consecutiveFailureCount,
            int maxAutoAttempts) {
    }

    private record LockedDeviceTask(
            long taskId,
            String state,
            UUID currentLeaseToken,
            LocalDateTime leaseUntil,
            long wakeVersion,
            long handledWakeVersion,
            int consecutiveFailureCount,
            int maxAutoAttempts,
            int attemptsForCurrentWake) {
    }

    private record LockedAttempt(
            long attemptId,
            UUID leaseToken,
            long claimedWakeVersion,
            String technicalResult) {
    }

    public record TaskExecution(
            long taskId,
            String state,
            UUID currentLeaseToken,
            LocalDateTime leaseUntil,
            long wakeVersion,
            long handledWakeVersion,
            int consecutiveFailureCount,
            int maxAutoAttempts,
            long inboxId,
            String inboxState,
            long attemptId,
            UUID attemptLeaseToken,
            long claimedWakeVersion,
            String technicalResult) {
    }

    public record DeviceTaskExecution(
            long taskId,
            String state,
            UUID currentLeaseToken,
            LocalDateTime leaseUntil,
            long wakeVersion,
            long handledWakeVersion,
            int consecutiveFailureCount,
            int maxAutoAttempts,
            int attemptsForCurrentWake,
            long attemptId,
            UUID attemptLeaseToken,
            long claimedWakeVersion,
            String technicalResult) {
    }

    private record WakeableTask(
            long id,
            String state,
            UUID leaseToken,
            LocalDateTime leaseUntil,
            long wakeVersion) {
    }
}
