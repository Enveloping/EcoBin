package org.enveloping.ecobin.device.application.delivery;

import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeliveryRecoveryQuarantineRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeliveryRecoveryQuarantineView;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * Safely closes one delivery whose physical outcome is unknowable.
 *
 * <p>The command and its terminal event deliberately live outside the normal
 * delivery-completion contract. Existing measurements and photos are archived
 * as issue evidence only. This service never writes a recycling order, wallet,
 * refund, review or withdrawal record.</p>
 */
@Service
public class DeliveryRecoveryQuarantineService {

    public static final String COMMAND_TYPE =
            "QUARANTINE_DELIVERY_RECOVERY";
    public static final String EVENT_TYPE =
            "DELIVERY_RECOVERY_QUARANTINED";
    public static final String TARGET_TYPE = "DELIVERY_SESSION";
    private static final String BUSINESS_VALUE = "NONE";
    private static final int MAX_AUTO_ATTEMPTS = 100;
    private static final String UUID_V4 =
            "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$";
    private static final String SHA256 = "^[0-9a-f]{64}$";

    static final String ABORT_SESSION_SQL = """
            UPDATE dev_delivery_session
            SET status = 'DEVICE_ABORTED',
                ended_at = ?,
                end_reason = 'REMOTE_RECOVERY_QUARANTINED',
                lock_version = lock_version + 1,
                updated_at = ?
            WHERE id = ?
              AND tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
              AND status = 'RESULT_PENDING_RECOVERY'
              AND device_completed_at IS NULL
              AND ended_at IS NULL
              AND end_reason IS NULL
              AND lock_version = ?
            """;
    static final String RELEASE_OCCUPANCY_SQL = """
            DELETE FROM dev_device_occupancy
            WHERE asset_id = ?
              AND tenant_id = ?
              AND organization_id = ?
              AND occupancy_kind = 'DELIVERY'
              AND delivery_session_id = ?
            """;
    static final String ARCHIVE_EVIDENCE_SQL = """
            UPDATE dev_delivery_recovery_quarantine
            SET state = 'APPLIED',
                business_value = 'NONE',
                source_inbox_id = ?,
                terminal_event_uid = ?,
                terminal_event_payload_sha256 = ?,
                resolution_evidence_sha256 = ?,
                operator_confirmations_json = ?,
                device_evidence_json = ?,
                existing_data_json = ?,
                terminal_payload_json = ?,
                applied_at = ?,
                lock_version = lock_version + 1,
                updated_at = ?
            WHERE id = ?
              AND state = 'QUEUED'
              AND business_value = 'NONE'
              AND lock_version = ?
            """;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final PlatformDeviceAssetTaskRefFactory taskRefFactory;
    private final ReliablePlatformDeviceControlTaskRegistrationPort tasks;
    private final ReliableDeviceTaskProofPort taskProof;

    public DeliveryRecoveryQuarantineService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeviceConfigurationCanonicalizer canonicalizer,
            PlatformDeviceAssetTaskRefFactory taskRefFactory,
            ReliablePlatformDeviceControlTaskRegistrationPort tasks,
            ReliableDeviceTaskProofPort taskProof) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.canonicalizer = canonicalizer;
        this.taskRefFactory = taskRefFactory;
        this.tasks = tasks;
        this.taskProof = taskProof;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeliveryRecoveryQuarantineView request(
            UUID recoveryUid,
            String hardwareSn,
            UUID sessionUid,
            long requestedByPlatformAdminId,
            DeliveryRecoveryQuarantineRequest request) {
        requireUuidV4(recoveryUid, "Idempotency-Key");
        requireUuidV4(sessionUid, "sessionUid");
        requireUuidV4(request.expectedTaskUid(), "expectedTaskUid");
        requireConfirmations(request);
        String reason = requiredText(request.reason(), 500, "reason");

        AssetRow asset = lockAsset(hardwareSn);
        if (asset.tenantId() == null || asset.organizationId() == null) {
            throw conflict(
                    "DEVICE.DELIVERY_RECOVERY_SCOPE_MISSING",
                    "设备尚未完成机构分配，不能处理投递异常");
        }
        if (!"ONLINE".equals(asset.connectionStatus())) {
            throw conflict(
                    "DEVICE.DELIVERY_RECOVERY_DEVICE_OFFLINE",
                    "设备当前未在线；请在设备重新通电并上线后重新核对现场，再发起异常收口");
        }

        SessionRow session = lockSession(asset.id(), sessionUid);
        if (request.expectedSessionVersion() == null
                || session.lockVersion()
                != request.expectedSessionVersion()) {
            throw conflict(
                    "COMMON.VERSION_CONFLICT",
                    "投递状态已经变化，请刷新后重新核对现场");
        }
        CommandTaskRow command = lockOriginalCommand(
                asset.id(), session.id());
        boolean exactOccupancy = lockExactOccupancy(
                asset, session.id());
        BusinessEvidence evidence = loadBusinessEvidence(
                command.commandId(), session.id());
        LocalDateTime now = databaseNow();
        if (!canRequest(
                session,
                command,
                exactOccupancy,
                evidence,
                asset.tenantId(),
                asset.organizationId(),
                asset.id(),
                request.expectedTaskUid(),
                sessionUid,
                now)) {
            throw recoveryConflict();
        }
        Integer active = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_delivery_recovery_quarantine
                        WHERE delivery_session_id = ?
                          AND state = 'QUEUED'
                        """,
                Integer.class,
                session.id());
        if (active == null || active != 0) {
            throw conflict(
                    "DEVICE.DELIVERY_RECOVERY_ALREADY_REQUESTED",
                    "该投递已经有一条异常收口指令，请查看原指令状态");
        }

        UUID recoveryCommandUid = UUID.randomUUID();
        Map<String, Object> payload = commandPayload(
                recoveryUid,
                sessionUid,
                command.commandUid(),
                request,
                reason);
        Map<String, Object> envelope = commandEnvelope(
                recoveryCommandUid,
                hardwareSn,
                sessionUid,
                now,
                payload);
        byte[] requestSha256 = canonicalizer.payloadSha256(payload);
        requireSingle(jdbc.update("""
                        INSERT INTO dev_delivery_recovery_quarantine (
                            recovery_uid,
                            request_sha256,
                            tenant_id,
                            organization_id,
                            asset_id,
                            delivery_session_id,
                            original_command_id,
                            original_command_uid,
                            original_task_uid,
                            recovery_command_uid,
                            recovery_task_uid,
                            requested_by_platform_admin_id,
                            expected_session_version,
                            physical_outcome_unknown_confirmed,
                            cause_fixed_confirmed,
                            device_power_cycled_confirmed,
                            motion_area_clear_confirmed,
                            delivery_door_closed_confirmed,
                            mechanism_clear_confirmed,
                            reason,
                            state,
                            business_value,
                            requested_at,
                            applied_at,
                            source_inbox_id,
                            terminal_event_uid,
                            terminal_event_payload_sha256,
                            resolution_evidence_sha256,
                            operator_confirmations_json,
                            device_evidence_json,
                            existing_data_json,
                            terminal_payload_json,
                            lock_version,
                            created_at,
                            updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?,
                            1, 1, 1, 1, 1, 1, ?,
                            'QUEUED', 'NONE', ?, NULL,
                            NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                            0, ?, ?
                        )
                        """,
                recoveryUid.toString(),
                requestSha256,
                asset.tenantId(),
                asset.organizationId(),
                asset.id(),
                session.id(),
                command.commandId(),
                command.commandUid().toString(),
                command.taskUid().toString(),
                recoveryCommandUid.toString(),
                requestedByPlatformAdminId,
                session.lockVersion(),
                reason,
                now,
                now,
                now));

        UUID taskUid = tasks.register(
                new ReliablePlatformDeviceControlTaskRegistration(
                        COMMAND_TYPE,
                        COMMAND_TYPE + ":"
                                + recoveryUid.toString().toUpperCase(),
                        TARGET_TYPE,
                        sessionUid.toString(),
                        taskRefFactory.issue(asset.id()),
                        2,
                        writeJson(envelope),
                        canonicalizer.payloadSha256(envelope),
                        recoveryUid,
                        command.commandUid(),
                        MAX_AUTO_ATTEMPTS));
        requireSingle(jdbc.update("""
                        UPDATE dev_delivery_recovery_quarantine
                        SET recovery_task_uid = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE recovery_uid = ?
                          AND state = 'QUEUED'
                          AND recovery_task_uid IS NULL
                        """,
                taskUid.toString(),
                now,
                recoveryUid.toString()));
        return new DeliveryRecoveryQuarantineView(
                recoveryUid,
                sessionUid,
                command.commandUid(),
                recoveryCommandUid,
                taskUid,
                "QUEUED",
                BUSINESS_VALUE,
                reason,
                null,
                instant(now),
                null,
                statusUrl(taskUid),
                null,
                confirmations(request),
                Map.of(),
                Map.of());
    }

    @Transactional(readOnly = true)
    public DeliveryRecoveryQuarantineView detail(
            String hardwareSn,
            UUID recoveryUid) {
        requireUuidV4(recoveryUid, "recoveryUid");
        List<RecoveryViewRow> rows = jdbc.query("""
                        SELECT recovery.recovery_uid,
                               session.session_uid,
                               recovery.original_command_uid,
                               recovery.recovery_command_uid,
                               recovery.recovery_task_uid,
                               recovery.state,
                               recovery.business_value,
                               recovery.reason,
                               port.port_no,
                               recovery.requested_at,
                               recovery.applied_at,
                               recovery.resolution_evidence_sha256,
                               recovery.operator_confirmations_json,
                               recovery.device_evidence_json,
                               recovery.existing_data_json
                        FROM dev_delivery_recovery_quarantine recovery
                        JOIN dev_device_asset asset
                          ON asset.id = recovery.asset_id
                        JOIN dev_delivery_session session
                          ON session.id = recovery.delivery_session_id
                        JOIN dev_port port
                          ON port.id = session.port_id
                        WHERE asset.hardware_sn = ?
                          AND recovery.recovery_uid = ?
                        """,
                (rs, ignored) -> recoveryViewRow(rs),
                hardwareSn,
                recoveryUid.toString());
        if (rows.size() != 1) {
            throw new TargetApiException(
                    404,
                    "DEVICE.DELIVERY_RECOVERY_NOT_FOUND",
                    "投递异常收口记录不存在");
        }
        return view(rows.getFirst());
    }

    /** Applies one dispatcher-validated platform fact under the asset lock. */
    @Transactional(propagation = Propagation.MANDATORY)
    public ApplyResult applyTrusted(
            long sourceInboxId,
            JsonNode normalized,
            LocalDateTime now) {
        JsonNode source = requiredObject(normalized, "trustedSource");
        JsonNode event = requiredObject(normalized, "event");
        JsonNode target = requiredObject(event, "target");
        JsonNode payload = requiredObject(event, "payload");
        String hardwareSn = requiredText(source, "deviceName", 64);
        requireExact(event, "eventType", EVENT_TYPE);
        requireExact(target, "type", TARGET_TYPE);
        UUID eventUid = requiredUuid(event, "eventUid");
        UUID recoveryCommandUid = requiredUuid(event, "commandUid");
        UUID recoveryUid = requiredUuid(payload, "recoveryUid");
        UUID sessionUid = requiredUuid(payload, "sessionUid");
        UUID originalCommandUid = requiredUuid(
                payload, "originalCommandUid");
        requireExact(payload, "businessValue", BUSINESS_VALUE);
        if (!eventUid.equals(recoveryUid)
                || !sessionUid.toString().equals(
                requiredText(target, "uid", 36))) {
            throw untrusted();
        }

        JsonNode operator = requiredObject(
                payload, "operatorConfirmations");
        requireAllConfirmations(operator);
        JsonNode deviceEvidence = requiredObject(
                payload, "deviceEvidence");
        if (!sessionUid.equals(requiredUuid(deviceEvidence, "workUid"))
                || !originalCommandUid.equals(
                requiredUuid(deviceEvidence, "commandUid"))
                || !originalCommandUid.equals(
                requiredUuid(deviceEvidence, "permitUid"))) {
            throw untrusted();
        }
        requireExact(deviceEvidence, "actionState", "ARMED");
        requireExact(
                deviceEvidence,
                "resolutionState",
                "UNKNOWN_EFFECT_QUARANTINED");
        String resolutionEvidenceSha256 = requiredPattern(
                deviceEvidence,
                "resolutionEvidenceSha256",
                SHA256);
        String eventPayloadSha256 = requiredPattern(
                event, "payloadSha256", SHA256);

        AssetRow asset = lockAsset(hardwareSn);
        RecoveryApplyRow recovery = lockRecovery(
                asset.id(), recoveryUid, sessionUid);
        if (recovery == null) {
            throw untrusted();
        }
        if ("APPLIED".equals(recovery.state())) {
            if (eventUid.equals(recovery.terminalEventUid())
                    && eventPayloadSha256.equals(
                    recovery.terminalEventPayloadSha256())) {
                return new ApplyResult(asset.id(), false);
            }
            throw untrusted();
        }
        if (!"QUEUED".equals(recovery.state())
                || !recoveryCommandUid.equals(
                recovery.recoveryCommandUid())
                || !originalCommandUid.equals(
                recovery.originalCommandUid())
                || !payload.path("reason").asText().equals(
                recovery.reason())
                || !"RESULT_PENDING_RECOVERY".equals(
                recovery.sessionStatus())
                || recovery.sessionEndedAt() != null
                || recovery.sessionEndReason() != null
                || payload.path("portNo").asInt(-1) != recovery.portNo()) {
            throw untrusted();
        }
        BusinessEvidence businessEvidence = loadBusinessEvidence(
                recovery.originalCommandId(),
                recovery.deliverySessionId());
        if (businessEvidence.physicalResultExists()
                || businessEvidence.deliveryOrderExists()
                || !lockExactOccupancy(asset, recovery.deliverySessionId())) {
            throw untrusted();
        }

        requireSingle(jdbc.update(
                ABORT_SESSION_SQL,
                now,
                now,
                recovery.deliverySessionId(),
                recovery.tenantId(),
                recovery.organizationId(),
                asset.id(),
                recovery.sessionLockVersion()));
        requireSingle(jdbc.update(
                RELEASE_OCCUPANCY_SQL,
                asset.id(),
                recovery.tenantId(),
                recovery.organizationId(),
                recovery.deliverySessionId()));

        Map<String, Object> existingData = new LinkedHashMap<>();
        existingData.put(
                "firstPreOpenMeasurement",
                jsonValue(payload.get("firstPreOpenMeasurement")));
        existingData.put(
                "finalPostCloseMeasurement",
                jsonValue(payload.get("finalPostCloseMeasurement")));
        existingData.put("photos", jsonValue(payload.get("photos")));
        requireSingle(jdbc.update(
                ARCHIVE_EVIDENCE_SQL,
                sourceInboxId,
                eventUid.toString(),
                hexBytes(eventPayloadSha256),
                hexBytes(resolutionEvidenceSha256),
                writeJson(jsonValue(operator)),
                writeJson(jsonValue(deviceEvidence)),
                writeJson(existingData),
                writeJson(jsonValue(payload)),
                now,
                now,
                recovery.id(),
                recovery.lockVersion()));
        taskProof.completeFromTrustedProof(
                COMMAND_TYPE,
                TARGET_TYPE,
                sessionUid.toString());
        return new ApplyResult(asset.id(), true);
    }

    static boolean canRequest(
            SessionRow session,
            CommandTaskRow command,
            boolean exactOccupancy,
            BusinessEvidence evidence,
            Long assetTenantId,
            Long assetOrganizationId,
            long assetId,
            UUID expectedTaskUid,
            UUID expectedSessionUid,
            LocalDateTime now) {
        boolean reliableTaskTerminal = command != null
                && (("DONE".equals(command.taskState())
                && command.blockedReasonCode() == null)
                || ("BLOCKED".equals(command.taskState())
                && "DEVICE_EVIDENCE_TIMEOUT".equals(
                command.blockedReasonCode())))
                && command.leaseToken() == null;
        return session != null
                && command != null
                && evidence != null
                && expectedTaskUid != null
                && expectedSessionUid != null
                && now != null
                && assetTenantId != null
                && assetOrganizationId != null
                && session.tenantId() == assetTenantId
                && session.organizationId() == assetOrganizationId
                && session.assetId() == assetId
                && expectedSessionUid.equals(session.sessionUid())
                && "RESULT_PENDING_RECOVERY".equals(session.status())
                && session.authorizationExpiresAt() != null
                && !session.authorizationExpiresAt().isAfter(now)
                && session.deviceCompletedAt() == null
                && session.endedAt() == null
                && session.endReason() == null
                && expectedTaskUid.equals(command.taskUid())
                && "START_DELIVERY_SESSION".equals(command.taskType())
                && TARGET_TYPE.equals(command.targetType())
                && expectedSessionUid.toString().equals(
                command.targetStableKey())
                && reliableTaskTerminal
                && List.of(
                        "QUEUED",
                        "EDGE_ACCEPTED",
                        "PHYSICAL_STARTED",
                        "EDGE_RESTARTED")
                .contains(command.physicalState())
                && exactOccupancy
                && !evidence.physicalResultExists()
                && !evidence.deliveryOrderExists();
    }

    private AssetRow lockAsset(String hardwareSn) {
        List<AssetRow> rows = jdbc.query("""
                        SELECT asset.id,
                               asset.tenant_id,
                               asset.organization_id,
                               transport.onenet_connection_status
                        FROM dev_device_asset asset
                        LEFT JOIN dev_device_transport_state transport
                          ON transport.asset_id = asset.id
                        WHERE asset.hardware_sn = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new AssetRow(
                        rs.getLong("id"),
                        nullableLong(rs, "tenant_id"),
                        nullableLong(rs, "organization_id"),
                        rs.getString("onenet_connection_status")),
                hardwareSn);
        if (rows.size() != 1) {
            throw new TargetApiException(
                    404,
                    "DEVICE.ASSET_NOT_FOUND",
                    "设备不存在或不可用");
        }
        return rows.getFirst();
    }

    private SessionRow lockSession(long assetId, UUID sessionUid) {
        List<SessionRow> rows = jdbc.query("""
                        SELECT id, session_uid, tenant_id, organization_id,
                               asset_id, status, authorization_expires_at,
                               first_physical_progress_at,
                               device_completed_at, ended_at, end_reason,
                               lock_version
                        FROM dev_delivery_session
                        WHERE asset_id = ?
                          AND session_uid = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new SessionRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("session_uid")),
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("asset_id"),
                        rs.getString("status"),
                        rs.getObject(
                                "authorization_expires_at",
                                LocalDateTime.class),
                        rs.getObject(
                                "first_physical_progress_at",
                                LocalDateTime.class),
                        rs.getObject(
                                "device_completed_at",
                                LocalDateTime.class),
                        rs.getObject("ended_at", LocalDateTime.class),
                        rs.getString("end_reason"),
                        rs.getLong("lock_version")),
                assetId,
                sessionUid.toString());
        if (rows.size() != 1) {
            throw new TargetApiException(
                    404,
                    "DEVICE.DELIVERY_SESSION_NOT_FOUND",
                    "该设备不存在对应的投递记录");
        }
        return rows.getFirst();
    }

    private CommandTaskRow lockOriginalCommand(
            long assetId,
            long sessionId) {
        List<CommandTaskRow> rows = jdbc.query("""
                        SELECT command_row.id AS command_id,
                               command_row.command_uid,
                               command_row.physical_state,
                               task.task_uid,
                               task.task_type,
                               task.state AS task_state,
                               task.blocked_reason_code,
                               task.target_type,
                               task.target_stable_key,
                               task.lease_token
                        FROM dev_device_command command_row
                        JOIN ops_reliable_task task
                          ON task.source_device_command_id = command_row.id
                        WHERE command_row.asset_id = ?
                          AND command_row.delivery_session_id = ?
                          AND command_row.command_type =
                              'START_DELIVERY_SESSION'
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CommandTaskRow(
                        rs.getLong("command_id"),
                        UUID.fromString(rs.getString("command_uid")),
                        rs.getString("physical_state"),
                        UUID.fromString(rs.getString("task_uid")),
                        rs.getString("task_type"),
                        rs.getString("task_state"),
                        rs.getString("blocked_reason_code"),
                        rs.getString("target_type"),
                        rs.getString("target_stable_key"),
                        rs.getString("lease_token")),
                assetId,
                sessionId);
        if (rows.size() != 1) {
            throw recoveryConflict();
        }
        return rows.getFirst();
    }

    private boolean lockExactOccupancy(
            AssetRow asset,
            long sessionId) {
        List<Long> rows = jdbc.query("""
                        SELECT delivery_session_id
                        FROM dev_device_occupancy
                        WHERE asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND occupancy_kind = 'DELIVERY'
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("delivery_session_id"),
                asset.id(),
                asset.tenantId(),
                asset.organizationId());
        return rows.size() == 1 && rows.getFirst() == sessionId;
    }

    private BusinessEvidence loadBusinessEvidence(
            long commandId,
            long sessionId) {
        return jdbc.queryForObject("""
                        SELECT EXISTS (
                                   SELECT 1
                                   FROM dev_physical_result physical_result
                                   WHERE physical_result.command_id = ?
                               ) AS physical_result_exists,
                               EXISTS (
                                   SELECT 1
                                   FROM rec_delivery_order delivery_order
                                   WHERE delivery_order.delivery_session_id = ?
                               ) AS delivery_order_exists
                        """,
                (rs, ignored) -> new BusinessEvidence(
                        rs.getBoolean("physical_result_exists"),
                        rs.getBoolean("delivery_order_exists")),
                commandId,
                sessionId);
    }

    private RecoveryApplyRow lockRecovery(
            long assetId,
            UUID recoveryUid,
            UUID sessionUid) {
        List<RecoveryApplyRow> rows = jdbc.query("""
                        SELECT recovery.id,
                               recovery.tenant_id,
                               recovery.organization_id,
                               recovery.delivery_session_id,
                               recovery.original_command_id,
                               recovery.original_command_uid,
                               recovery.recovery_command_uid,
                               recovery.state,
                               recovery.reason,
                               recovery.terminal_event_uid,
                               HEX(recovery.terminal_event_payload_sha256)
                                   AS terminal_payload_sha256,
                               recovery.lock_version,
                               session.status AS session_status,
                               session.ended_at AS session_ended_at,
                               session.end_reason AS session_end_reason,
                               session.lock_version AS session_lock_version,
                               port.port_no
                        FROM dev_delivery_recovery_quarantine recovery
                        JOIN dev_delivery_session session
                          ON session.id = recovery.delivery_session_id
                         AND session.asset_id = recovery.asset_id
                        JOIN dev_port port
                          ON port.id = session.port_id
                         AND port.asset_id = session.asset_id
                        WHERE recovery.asset_id = ?
                          AND recovery.recovery_uid = ?
                          AND session.session_uid = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new RecoveryApplyRow(
                        rs.getLong("id"),
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("delivery_session_id"),
                        rs.getLong("original_command_id"),
                        UUID.fromString(
                                rs.getString("original_command_uid")),
                        UUID.fromString(
                                rs.getString("recovery_command_uid")),
                        rs.getString("state"),
                        rs.getString("reason"),
                        optionalUuid(rs.getString("terminal_event_uid")),
                        lower(rs.getString("terminal_payload_sha256")),
                        rs.getLong("lock_version"),
                        rs.getString("session_status"),
                        rs.getObject(
                                "session_ended_at",
                                LocalDateTime.class),
                        rs.getString("session_end_reason"),
                        rs.getLong("session_lock_version"),
                        rs.getInt("port_no")),
                assetId,
                recoveryUid.toString(),
                sessionUid.toString());
        return rows.size() == 1 ? rows.getFirst() : null;
    }

    private Map<String, Object> commandPayload(
            UUID recoveryUid,
            UUID sessionUid,
            UUID originalCommandUid,
            DeliveryRecoveryQuarantineRequest request,
            String reason) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("recoveryUid", recoveryUid.toString());
        payload.put("sessionUid", sessionUid.toString());
        payload.put(
                "originalCommandUid",
                originalCommandUid.toString());
        payload.put("physicalOutcomeUnknownConfirmed", true);
        payload.put("causeFixedConfirmed", true);
        payload.put("devicePowerCycledConfirmed", true);
        payload.put("motionAreaClearConfirmed", true);
        payload.put("deliveryDoorClosedConfirmed", true);
        payload.put("mechanismClearConfirmed", true);
        payload.put("reason", reason);
        return payload;
    }

    private Map<String, Object> commandEnvelope(
            UUID commandUid,
            String hardwareSn,
            UUID sessionUid,
            LocalDateTime issuedAt,
            Map<String, Object> payload) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", COMMAND_TYPE);
        envelope.put("targetDeviceName", hardwareSn);
        envelope.put("target", Map.of(
                "type", TARGET_TYPE,
                "uid", sessionUid.toString()));
        envelope.put("issuedAt", instant(issuedAt).toString());
        envelope.put(
                "expiresAt",
                instant(issuedAt.plusMinutes(5)).toString());
        envelope.put("payloadSchemaVersion", 2);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(
                        canonicalizer.payloadSha256(payload)));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        return envelope;
    }

    private static void requireConfirmations(
            DeliveryRecoveryQuarantineRequest request) {
        if (request == null
                || !Boolean.TRUE.equals(
                request.physicalOutcomeUnknownConfirmed())
                || !Boolean.TRUE.equals(request.causeFixedConfirmed())
                || !Boolean.TRUE.equals(
                request.devicePowerCycledConfirmed())
                || !Boolean.TRUE.equals(
                request.motionAreaClearConfirmed())
                || !Boolean.TRUE.equals(
                request.deliveryDoorClosedConfirmed())
                || !Boolean.TRUE.equals(
                request.mechanismClearConfirmed())) {
            throw invalid(
                    "必须确认原物理结果未知、设备已重新上电、故障已排除，且投递门关闭、机构无卡物、运动范围无人");
        }
    }

    private static Map<String, Object> confirmations(
            DeliveryRecoveryQuarantineRequest request) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("physicalOutcomeUnknownConfirmed", true);
        result.put("causeFixedConfirmed", true);
        result.put("devicePowerCycledConfirmed", true);
        result.put("motionAreaClearConfirmed", true);
        result.put("deliveryDoorClosedConfirmed", true);
        result.put("mechanismClearConfirmed", true);
        return result;
    }

    private static void requireAllConfirmations(JsonNode operator) {
        for (String field : List.of(
                "physicalOutcomeUnknownConfirmed",
                "causeFixedConfirmed",
                "devicePowerCycledConfirmed",
                "motionAreaClearConfirmed",
                "deliveryDoorClosedConfirmed",
                "mechanismClearConfirmed")) {
            JsonNode value = operator.get(field);
            if (value == null
                    || !value.isBoolean()
                    || !value.asBoolean()) {
                throw untrusted();
            }
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> jsonObject(String json) {
        if (json == null) {
            return Map.of();
        }
        return objectMapper.convertValue(
                objectMapper.readTree(json), Map.class);
    }

    private Object jsonValue(JsonNode value) {
        if (value == null || value.isNull()) {
            return null;
        }
        return objectMapper.convertValue(value, Object.class);
    }

    private RecoveryViewRow recoveryViewRow(ResultSet rs)
            throws SQLException {
        return new RecoveryViewRow(
                UUID.fromString(rs.getString("recovery_uid")),
                UUID.fromString(rs.getString("session_uid")),
                UUID.fromString(rs.getString("original_command_uid")),
                UUID.fromString(rs.getString("recovery_command_uid")),
                optionalUuid(rs.getString("recovery_task_uid")),
                rs.getString("state"),
                rs.getString("business_value"),
                rs.getString("reason"),
                rs.getInt("port_no"),
                rs.getObject("requested_at", LocalDateTime.class),
                rs.getObject("applied_at", LocalDateTime.class),
                lower(rs.getString("resolution_evidence_sha256")),
                jsonObject(rs.getString("operator_confirmations_json")),
                jsonObject(rs.getString("device_evidence_json")),
                jsonObject(rs.getString("existing_data_json")));
    }

    private static DeliveryRecoveryQuarantineView view(
            RecoveryViewRow row) {
        return new DeliveryRecoveryQuarantineView(
                row.recoveryUid(),
                row.sessionUid(),
                row.originalCommandUid(),
                row.recoveryCommandUid(),
                row.recoveryTaskUid(),
                row.state(),
                row.businessValue(),
                row.reason(),
                row.portNo(),
                instant(row.requestedAt()),
                instant(row.appliedAt()),
                row.recoveryTaskUid() == null
                        ? null : statusUrl(row.recoveryTaskUid()),
                row.evidenceSha256(),
                row.operatorConfirmations(),
                row.deviceEvidence(),
                row.existingData());
    }

    private LocalDateTime databaseNow() {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (now == null) {
            throw new IllegalStateException(
                    "database time is unavailable");
        }
        return now;
    }

    private String writeJson(Object value) {
        return objectMapper.writeValueAsString(value);
    }

    private static JsonNode requiredObject(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isObject()) {
            throw untrusted();
        }
        return value;
    }

    private static UUID requiredUuid(JsonNode parent, String field) {
        return UUID.fromString(requiredPattern(parent, field, UUID_V4));
    }

    private static String requiredPattern(
            JsonNode parent,
            String field,
            String pattern) {
        String value = requiredText(parent, field, 160);
        if (!value.matches(pattern)) {
            throw untrusted();
        }
        return value;
    }

    private static String requiredText(
            JsonNode parent,
            String field,
            int maximumLength) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null
                || !value.isTextual()
                || value.asText().isBlank()
                || value.asText().length() > maximumLength) {
            throw untrusted();
        }
        return value.asText();
    }

    private static String requiredText(
            String value,
            int maximumLength,
            String field) {
        if (value == null
                || value.isBlank()
                || value.length() > maximumLength) {
            throw invalid(field + " 不能为空且不能超过 "
                    + maximumLength + " 个字符");
        }
        return value.trim();
    }

    private static void requireExact(
            JsonNode parent,
            String field,
            String expected) {
        if (!expected.equals(requiredText(parent, field, 160))) {
            throw untrusted();
        }
    }

    private static void requireUuidV4(UUID value, String field) {
        if (value == null || value.version() != 4
                || value.variant() != 2) {
            throw invalid(field + " 必须是 UUIDv4");
        }
    }

    private static byte[] hexBytes(String value) {
        return java.util.HexFormat.of().parseHex(value);
    }

    private static String lower(String value) {
        return value == null ? null : value.toLowerCase();
    }

    private static Long nullableLong(ResultSet rs, String field)
            throws SQLException {
        long value = rs.getLong(field);
        return rs.wasNull() ? null : value;
    }

    private static UUID optionalUuid(String value) {
        return value == null ? null : UUID.fromString(value);
    }

    private static Instant instant(LocalDateTime value) {
        return value == null
                ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static String statusUrl(UUID taskUid) {
        return "/api/v1/web/platform/operations/reliable-tasks/"
                + taskUid;
    }

    private static void requireSingle(int updated) {
        if (updated != 1) {
            throw new IllegalStateException(
                    "delivery recovery mutation expected one row");
        }
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(
                400,
                "COMMON.INVALID_REQUEST",
                message,
                false,
                Map.of());
    }

    private static TargetApiException conflict(
            String code,
            String message) {
        return new TargetApiException(409, code, message);
    }

    private static TargetApiException recoveryConflict() {
        return conflict(
                "DEVICE.DELIVERY_RECOVERY_QUARANTINE_NOT_ALLOWED",
                "投递事实已经变化，或该记录不再满足异常隔离收口条件；请刷新后重新核对现场");
    }

    private static UntrustedInboxSourceException untrusted() {
        return new UntrustedInboxSourceException(
                "delivery recovery quarantine fact is untrusted");
    }

    record SessionRow(
            long id,
            UUID sessionUid,
            long tenantId,
            long organizationId,
            long assetId,
            String status,
            LocalDateTime authorizationExpiresAt,
            LocalDateTime firstPhysicalProgressAt,
            LocalDateTime deviceCompletedAt,
            LocalDateTime endedAt,
            String endReason,
            long lockVersion) {
    }

    record CommandTaskRow(
            long commandId,
            UUID commandUid,
            String physicalState,
            UUID taskUid,
            String taskType,
            String taskState,
            String blockedReasonCode,
            String targetType,
            String targetStableKey,
            String leaseToken) {
    }

    record BusinessEvidence(
            boolean physicalResultExists,
            boolean deliveryOrderExists) {
    }

    public record ApplyResult(long assetId, boolean changed) {
    }

    private record AssetRow(
            long id,
            Long tenantId,
            Long organizationId,
            String connectionStatus) {
    }

    private record RecoveryApplyRow(
            long id,
            long tenantId,
            long organizationId,
            long deliverySessionId,
            long originalCommandId,
            UUID originalCommandUid,
            UUID recoveryCommandUid,
            String state,
            String reason,
            UUID terminalEventUid,
            String terminalEventPayloadSha256,
            long lockVersion,
            String sessionStatus,
            LocalDateTime sessionEndedAt,
            String sessionEndReason,
            long sessionLockVersion,
            int portNo) {
    }

    private record RecoveryViewRow(
            UUID recoveryUid,
            UUID sessionUid,
            UUID originalCommandUid,
            UUID recoveryCommandUid,
            UUID recoveryTaskUid,
            String state,
            String businessValue,
            String reason,
            int portNo,
            LocalDateTime requestedAt,
            LocalDateTime appliedAt,
            String evidenceSha256,
            Map<String, Object> operatorConfirmations,
            Map<String, Object> deviceEvidence,
            Map<String, Object> existingData) {
    }
}
