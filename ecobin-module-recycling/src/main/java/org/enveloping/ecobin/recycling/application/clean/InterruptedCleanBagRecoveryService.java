package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.device.api.port.DeviceCommandCanonicalizationPort;
import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.reliability.DeviceCommandTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistrationPort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.StartCleanIdentityParticipationPort;
import org.enveloping.ecobin.identity.api.result.LockedCleanOrganizationUser;
import org.enveloping.ecobin.identity.api.result.LockedMiniappCleanScope;
import org.enveloping.ecobin.recycling.application.bag.Eb1BagCodeService;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.InterruptedCleanBagRecoveryAccepted;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.RecoverInterruptedCleanBagRequest;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.PreparedStatementCreator;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/**
 * Records the human-observed bag after an interrupted clean.
 *
 * <p>The deliberately small recovery surface accepts only the old bag frozen
 * by the original clean, or that clean's already-reserved new bag.  A third
 * bag must be physically replaced with one of those two before retrying.  The
 * old-bag branch performs no device action.  The new-bag branch creates an
 * independent empty-bag measurement and remains blocked until its trusted
 * result is applied.</p>
 */
@Service
public class InterruptedCleanBagRecoveryService {

    static final String TASK_TYPE = "MEASURE_EMPTY_BAG_BASELINE";
    static final String TARGET_TYPE = "BASELINE_MEASUREMENT";
    static final String ACTION = "clean.interrupted-bag.recover";
    private static final long COMMAND_VALIDITY_SECONDS = 60;
    static final long BASELINE_MEASUREMENT_TIMEOUT_MS = 5_000;
    private static final Set<String> SUPPORTED_RECOVERY_FAULTS = Set.of(
            "MCU_RESTART_FINAL_RESULT_UNAVAILABLE",
            "MCU_COMMUNICATION_UNAVAILABLE",
            "EDGE_RESTARTED",
            "MCU_CLEAN_FINAL_WEIGHT_UNAVAILABLE",
            "MCU_WORK_CANCELLED",
            "MCU_WORK_FAILED");

    static final String LOCK_TARGET_SQL = """
            SELECT operation.id, operation.operation_uid,
                   operation.tenant_id, operation.organization_id,
                   operation.asset_id, operation.port_id,
                   operation.cleaner_organization_user_id,
                   operation.status, operation.lock_version,
                   operation.end_reason,
                   operation.old_bag_id,
                   operation.old_baseline_id,
                   operation.old_baseline_weight_g,
                   operation.new_bag_id,
                   operation.offline_occupancy_released_at,
                   asset.hardware_sn,
                   port.port_no,
                   old_bag.bag_uid AS old_bag_uid,
                   old_bag.bag_code AS old_bag_code,
                   new_bag.bag_uid AS new_bag_uid,
                   new_bag.bag_code AS new_bag_code,
                   config.id AS config_id,
                   config.version_no AS config_version,
                   LOWER(HEX(config.content_sha256)) AS content_sha256,
                   LOWER(HEX(config.mcu_payload_sha256))
                       AS mcu_payload_sha256,
                   snapshot.id AS snapshot_id,
                   snapshot.calibration_version,
                   snapshot.fullness_mode,
                   snapshot.configured_full_weight_g,
                   snapshot.fullness_settle_wait_ms,
                   snapshot.fullness_confirmation_wait_ms,
                   snapshot.weight_measurement_timeout_ms,
                   capacity.lock_version AS capacity_version,
                   capacity.current_bag_id,
                   capacity.baseline_state,
                   capacity.current_baseline_id,
                   capacity.current_baseline_weight_g,
                   baseline.bag_id AS baseline_bag_id,
                   baseline.baseline_weight_g AS baseline_weight_g,
                   runtime.applied_config_version_no,
                   LOWER(HEX(runtime.applied_config_content_sha256))
                       AS applied_content_sha256,
                   LOWER(HEX(runtime.applied_mcu_payload_sha256))
                       AS applied_mcu_payload_sha256
            FROM rec_clean_operation operation
            JOIN dev_device_asset asset
              ON asset.id = operation.asset_id
             AND asset.tenant_id = operation.tenant_id
             AND asset.organization_id = operation.organization_id
            JOIN dev_port port
              ON port.id = operation.port_id
             AND port.asset_id = operation.asset_id
             AND port.tenant_id = operation.tenant_id
             AND port.organization_id = operation.organization_id
            LEFT JOIN rec_bag old_bag
              ON old_bag.id = operation.old_bag_id
             AND old_bag.tenant_id = operation.tenant_id
             AND old_bag.organization_id = operation.organization_id
            JOIN rec_bag new_bag
              ON new_bag.id = operation.new_bag_id
             AND new_bag.tenant_id = operation.tenant_id
             AND new_bag.organization_id = operation.organization_id
            JOIN dev_config_version config
              ON config.id = operation.device_config_version_id
             AND config.asset_id = operation.asset_id
             AND config.tenant_id = operation.tenant_id
             AND config.organization_id = operation.organization_id
            JOIN dev_port_config_snapshot snapshot
              ON snapshot.config_version_id = config.id
             AND snapshot.port_id = operation.port_id
             AND snapshot.asset_id = operation.asset_id
             AND snapshot.tenant_id = operation.tenant_id
             AND snapshot.organization_id = operation.organization_id
            JOIN rec_port_capacity_state capacity
              ON capacity.port_id = operation.port_id
             AND capacity.asset_id = operation.asset_id
             AND capacity.tenant_id = operation.tenant_id
             AND capacity.organization_id = operation.organization_id
            LEFT JOIN rec_port_weight_baseline baseline
              ON baseline.id = capacity.current_baseline_id
             AND baseline.port_id = capacity.port_id
             AND baseline.tenant_id = capacity.tenant_id
             AND baseline.organization_id = capacity.organization_id
            JOIN dev_device_runtime_state runtime
              ON runtime.asset_id = operation.asset_id
             AND runtime.tenant_id = operation.tenant_id
             AND runtime.organization_id = operation.organization_id
            WHERE operation.operation_uid = ?
              AND operation.tenant_id = ?
              AND operation.organization_id = ?
            FOR UPDATE
            """;

    private static final String LOAD_RECOVERY_COORDINATES_SQL = """
            SELECT asset_id, port_id
            FROM rec_clean_operation
            WHERE operation_uid = ?
              AND tenant_id = ?
              AND organization_id = ?
            """;

    private static final String LOCK_RECOVERY_RUNTIME_SQL = """
            SELECT asset_id
            FROM dev_device_runtime_state
            WHERE tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
            FOR UPDATE
            """;

    private static final String LOCK_RECOVERY_CAPACITY_SQL = """
            SELECT port_id
            FROM rec_port_capacity_state
            WHERE tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
              AND port_id = ?
            FOR UPDATE
            """;

    private final JdbcTemplate jdbc;
    private final StartCleanIdentityParticipationPort identity;
    private final ReliableDeviceTaskRegistrationPort taskRegistration;
    private final DeviceCommandTaskRefFactory taskRefFactory;
    private final DeviceCommandCanonicalizationPort canonicalizer;
    private final AuditPort audit;
    private final ObjectMapper objectMapper;
    private final Eb1BagCodeService bagCodes;

    public InterruptedCleanBagRecoveryService(
            JdbcTemplate jdbc,
            StartCleanIdentityParticipationPort identity,
            ReliableDeviceTaskRegistrationPort taskRegistration,
            DeviceCommandTaskRefFactory taskRefFactory,
            DeviceCommandCanonicalizationPort canonicalizer,
            AuditPort audit,
            ObjectMapper objectMapper,
            Eb1BagCodeService bagCodes) {
        this.jdbc = jdbc;
        this.identity = identity;
        this.taskRegistration = taskRegistration;
        this.taskRefFactory = taskRefFactory;
        this.canonicalizer = canonicalizer;
        this.audit = audit;
        this.objectMapper = objectMapper;
        this.bagCodes = bagCodes;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public InterruptedCleanBagRecoveryAccepted recover(
            UUID idempotencyKey,
            UUID operationUid,
            RecoverInterruptedCleanBagRequest request) {
        requireUuidV4(idempotencyKey, "Idempotency-Key");
        requireUuidV4(operationUid, "operationUid");
        if (request == null
                || !Boolean.TRUE.equals(request.actualBagConfirmed())) {
            throw invalid(
                    "CLEAN.ACTUAL_BAG_CONFIRMATION_REQUIRED",
                    "必须先现场核对并确认设备内实际安装的袋");
        }
        String bagCode = bagCodes.authenticate(request.actualBagQr())
                .orElseThrow(() -> invalid(
                        "CLEAN.BAG_CODE_INVALID",
                        "实际袋码未通过平台防伪校验"))
                .value();
        String reason = request.reason().trim();
        LockedMiniappCleanScope scope = identity.lockCurrentMiniappScope();
        LockedCleanOrganizationUser cleaner =
                identity.lockCurrentCleaner(scope);
        String fingerprint = fingerprint(
                cleaner.organizationUserUid().value(),
                operationUid,
                bagCode,
                request.emptyBagConfirmed(),
                request.expectedOperationVersion(),
                reason);
        return cleaner.organizationUserRef().withOrganizationUserOnce(
                (tenantId, organizationId, organizationUserId) ->
                        recoverScoped(
                                idempotencyKey,
                                operationUid,
                                request,
                                bagCode,
                                reason,
                                fingerprint,
                                cleaner,
                                tenantId,
                                organizationId,
                                organizationUserId));
    }

    private InterruptedCleanBagRecoveryAccepted recoverScoped(
            UUID idempotencyKey,
            UUID operationUid,
            RecoverInterruptedCleanBagRequest request,
            String bagCode,
            String reason,
            String fingerprint,
            LockedCleanOrganizationUser cleaner,
            long tenantId,
            long organizationId,
            long organizationUserId) {
        Optional<SuccessfulAudit> previous =
                audit.findSuccessful(idempotencyKey);
        if (previous.isPresent()) {
            return replay(
                    previous.orElseThrow(),
                    fingerprint,
                    tenantId,
                    organizationId,
                    organizationUserId,
                    operationUid);
        }
        RecoveryCoordinates coordinates = one(
                LOAD_RECOVERY_COORDINATES_SQL,
                (rs, ignored) -> new RecoveryCoordinates(
                        rs.getLong("asset_id"),
                        rs.getLong("port_id")),
                operationUid.toString(),
                tenantId,
                organizationId).orElseThrow(
                InterruptedCleanBagRecoveryService::notFound);
        lockSingle(
                LOCK_RECOVERY_RUNTIME_SQL,
                "device runtime",
                tenantId,
                organizationId,
                coordinates.assetId());
        lockSingle(
                LOCK_RECOVERY_CAPACITY_SQL,
                "port capacity",
                tenantId,
                organizationId,
                coordinates.assetId(),
                coordinates.portId());
        Target target = one(
                LOCK_TARGET_SQL,
                (rs, ignored) -> target(rs),
                operationUid.toString(),
                tenantId,
                organizationId).orElseThrow(
                InterruptedCleanBagRecoveryService::notFound);
        if (target.assetId() != coordinates.assetId()
                || target.portId() != coordinates.portId()) {
            throw conflict(
                    "CLEAN.RECOVERY_STATE_CHANGED",
                    "清运所属设备或投口已变化，请刷新后重试");
        }
        if (target.cleanerOrganizationUserId() != organizationUserId) {
            throw notFound();
        }
        if (target.lockVersion()
                != request.expectedOperationVersion()) {
            throw conflict(
                    "CLEAN.RECOVERY_VERSION_CHANGED",
                    "清运状态已变化，请刷新后重新确认");
        }
        ExistingRecovery existing = loadExistingRecovery(
                target.id(), tenantId, organizationId);
        if (existing == null
                && !"ABORTED".equals(target.status())) {
            throw conflict(
                    "CLEAN.RECOVERY_NOT_REQUIRED",
                    "该清运不处于等待人工确认袋状态");
        }
        if (existing != null
                && !"ABORTED".equals(target.status())) {
            throw conflict(
                    "CLEAN.RECOVERY_STATE_CHANGED",
                    "该中断清运的人工处理状态已变化");
        }
        Decision decision = decide(target, bagCode);
        if (decision == null) {
            throw invalid(
                    "CLEAN.RECOVERY_BAG_NOT_ALLOWED",
                    "恢复只接受原袋或本次清运已预留的新袋；请先换回其中一只袋");
        }
        if (decision == Decision.USE_RESERVED_NEW_BAG
                && !Boolean.TRUE.equals(request.emptyBagConfirmed())) {
            throw invalid(
                    "CLEAN.EMPTY_BAG_CONFIRMATION_REQUIRED",
                    "换入预留新袋时必须确认袋内为空，才能建立新皮重");
        }

        InterruptedCleanBagRecoveryAccepted response;
        if (existing != null) {
            requireSameRecovery(existing, target, decision);
            if (!"COMPLETED".equals(existing.status())) {
                requireExactInterlock(target, tenantId, organizationId);
            }
            response = continueExisting(
                    idempotencyKey,
                    target,
                    existing,
                    decision,
                    tenantId,
                    organizationId);
        } else if (decision == Decision.RETAIN_OLD_BAG) {
            requireExactInterlock(target, tenantId, organizationId);
            String faultCode = sourceFaultCode(
                    target, tenantId, organizationId);
            UUID recoveryUid = UUID.randomUUID();
            completeRetainedOldBag(
                    recoveryUid,
                    target,
                    faultCode,
                    organizationUserId,
                    reason,
                    tenantId,
                    organizationId);
            response = response(
                    recoveryUid,
                    target,
                    decision,
                    "COMPLETED",
                    null,
                    null);
        } else {
            requireExactInterlock(target, tenantId, organizationId);
            String faultCode = sourceFaultCode(
                    target, tenantId, organizationId);
            UUID recoveryUid = UUID.randomUUID();
            long recoveryId = insertRecovery(
                    recoveryUid,
                    target,
                    decision,
                    "BASELINE_PENDING",
                    faultCode,
                    organizationUserId,
                    reason,
                    null,
                    tenantId,
                    organizationId);
            LocalDateTime now = databaseNow();
            requireOriginalOperationEnded(target, faultCode);
            markPhotosMissing(
                    target, faultCode, tenantId, organizationId, now);
            BaselineAttempt attempt = createBaselineAttempt(
                    idempotencyKey,
                    recoveryId,
                    target,
                    tenantId,
                    organizationId);
            response = response(
                    recoveryUid,
                    target,
                    decision,
                    "BASELINE_PENDING",
                    attempt.measurementUid(),
                    attempt.taskUid());
        }
        appendAudit(
                idempotencyKey,
                fingerprint,
                response,
                cleaner,
                tenantId,
                organizationId,
                organizationUserId,
                operationUid);
        return response;
    }

    private InterruptedCleanBagRecoveryAccepted continueExisting(
            UUID correlationUid,
            Target target,
            ExistingRecovery existing,
            Decision decision,
            long tenantId,
            long organizationId) {
        if (decision == Decision.RETAIN_OLD_BAG
                || "COMPLETED".equals(existing.status())) {
            return response(
                    existing.recoveryUid(),
                    target,
                    decision,
                    existing.status(),
                    existing.measurementUid(),
                    existing.taskUid());
        }
        if ("BASELINE_PENDING".equals(existing.status())) {
            if (existing.measurementUid() == null
                    || existing.taskUid() == null) {
                throw new IllegalStateException(
                        "pending clean recovery lost its baseline task");
            }
            return response(
                    existing.recoveryUid(),
                    target,
                    decision,
                    existing.status(),
                    existing.measurementUid(),
                    existing.taskUid());
        }
        if (!"BASELINE_REQUIRED".equals(existing.status())) {
            throw new IllegalStateException(
                    "clean bag recovery has an unsupported state");
        }
        BaselineAttempt attempt = createBaselineAttempt(
                correlationUid,
                existing.id(),
                target,
                tenantId,
                organizationId);
        requireSingle(jdbc.update("""
                        UPDATE rec_clean_bag_recovery
                        SET status = 'BASELINE_PENDING',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND status = 'BASELINE_REQUIRED'
                        """,
                databaseNow(),
                existing.id(),
                tenantId,
                organizationId),
                "restart interrupted-clean baseline");
        return response(
                existing.recoveryUid(),
                target,
                decision,
                "BASELINE_PENDING",
                attempt.measurementUid(),
                attempt.taskUid());
    }

    private void completeRetainedOldBag(
            UUID recoveryUid,
            Target target,
            String faultCode,
            long organizationUserId,
            String reason,
            long tenantId,
            long organizationId) {
        if (target.oldBagId() == null
                || target.oldBaselineId() == null
                || target.oldBaselineWeightGrams() == null
                || !Objects.equals(target.oldBagId(), target.currentBagId())
                || !"VALID".equals(target.baselineState())
                || !Objects.equals(
                        target.oldBaselineId(), target.currentBaselineId())
                || !Objects.equals(
                        target.oldBaselineWeightGrams(),
                        target.currentBaselineWeightGrams())
                || !Objects.equals(target.oldBagId(), target.baselineBagId())
                || !Objects.equals(
                        target.oldBaselineWeightGrams(),
                        target.baselineWeightGrams())) {
            throw conflict(
                    "CLEAN.OLD_BAG_BASELINE_CHANGED",
                    "原袋或原皮重已变化，不能按保留原袋收口");
        }
        requireCurrentBagOccupancy(target, tenantId, organizationId);
        boolean legacyEdgeRestart = "EDGE_RESTARTED".equals(faultCode);
        if (!legacyEdgeRestart) {
            requireReservedNewBag(target, tenantId, organizationId);
        }
        LocalDateTime now = databaseNow();
        insertRecovery(
                recoveryUid,
                target,
                Decision.RETAIN_OLD_BAG,
                "COMPLETED",
                faultCode,
                organizationUserId,
                reason,
                now,
                tenantId,
                organizationId);
        requireOriginalOperationEnded(target, faultCode);
        releaseDeviceOccupancy(
                target, legacyEdgeRestart, tenantId, organizationId);
        releaseReservedNewBag(
                target, legacyEdgeRestart, tenantId, organizationId, now);
        releaseInterlock(target, tenantId, organizationId);
        markPhotosMissing(target, faultCode, tenantId, organizationId, now);
    }

    private long insertRecovery(
            UUID recoveryUid,
            Target target,
            Decision decision,
            String status,
            String faultCode,
            long organizationUserId,
            String reason,
            LocalDateTime completedAt,
            long tenantId,
            long organizationId) {
        LocalDateTime now = databaseNow();
        return insertAndReturnKey("""
                INSERT INTO rec_clean_bag_recovery (
                    recovery_uid, tenant_id, organization_id,
                    asset_id, port_id, clean_operation_id,
                    actual_bag_id, decision, status,
                    source_fault_code,
                    cleaner_organization_user_id,
                    operator_reason,
                    requested_at, completed_at,
                    lock_version, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, 0, ?, ?
                )
                """,
                recoveryUid.toString(),
                tenantId,
                organizationId,
                target.assetId(),
                target.portId(),
                target.id(),
                decision == Decision.RETAIN_OLD_BAG
                        ? target.oldBagId() : target.newBagId(),
                decision.name(),
                status,
                faultCode,
                organizationUserId,
                reason,
                now,
                completedAt == null ? null : now,
                now,
                now);
    }

    private BaselineAttempt createBaselineAttempt(
            UUID correlationUid,
            long recoveryId,
            Target target,
            long tenantId,
            long organizationId) {
        requireBaselinePrerequisites(target, tenantId, organizationId);
        LocalDateTime now = databaseNow();
        UUID measurementUid = UUID.randomUUID();
        long measurementId = insertAndReturnKey("""
                INSERT INTO rec_port_baseline_measurement (
                    measurement_uid, tenant_id, organization_id,
                    asset_id, port_id, bag_id,
                    device_config_version_id,
                    port_config_snapshot_id,
                    capacity_lock_version_snapshot,
                    fullness_rule_fingerprint,
                    initiator_kind, platform_admin_id,
                    staff_account_id, clean_bag_recovery_id,
                    status, physical_result_id,
                    stable_total_weight_g, fault_code,
                    result_baseline_id, started_at,
                    completed_at, lock_version,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'SYSTEM', NULL, NULL, ?,
                    'PENDING', NULL, NULL, NULL, NULL,
                    ?, NULL, 0, ?, ?
                )
                """,
                measurementUid.toString(),
                tenantId,
                organizationId,
                target.assetId(),
                target.portId(),
                target.newBagId(),
                target.configurationId(),
                target.snapshotId(),
                target.capacityVersion(),
                fullnessRuleFingerprint(target),
                recoveryId,
                now,
                now,
                now);

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("version", target.configurationVersion());
        config.put("contentSha256", target.contentSha256());
        config.put("mcuPayloadSha256", target.mcuPayloadSha256());
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("measurementUid", measurementUid.toString());
        payload.put("portNo", target.portNo());
        payload.put("bagUid", target.newBagUid().toString());
        payload.put("emptyBagConfirmed", true);
        payload.put(
                "measurementTimeoutMs",
                BASELINE_MEASUREMENT_TIMEOUT_MS);
        payload.put("config", config);
        byte[] payloadSha256 = canonicalizer.payloadSha256(payload);
        UUID commandUid = UUID.randomUUID();
        Map<String, Object> envelope = commandEnvelope(
                commandUid,
                target,
                measurementUid,
                now,
                payload,
                payloadSha256);
        byte[] envelopeSha256 = canonicalizer.payloadSha256(envelope);
        long commandId = insertCommand(
                commandUid,
                measurementId,
                target,
                writeJson(envelope),
                envelopeSha256,
                tenantId,
                organizationId,
                now);
        UUID taskUid = registerTask(
                correlationUid,
                commandUid,
                measurementUid,
                commandId,
                target,
                envelopeSha256);
        return new BaselineAttempt(measurementUid, taskUid);
    }

    private void requireBaselinePrerequisites(
            Target target,
            long tenantId,
            long organizationId) {
        requireCurrentBagOccupancy(target, tenantId, organizationId);
        requireReservedNewBag(target, tenantId, organizationId);
        if (!Objects.equals(
                    target.configurationVersion(),
                    target.appliedConfigurationVersion())
                || !target.contentSha256().equals(
                        target.appliedContentSha256())
                || !target.mcuPayloadSha256().equals(
                        target.appliedMcuPayloadSha256())) {
            throw conflict(
                    "DEVICE.CONFIGURATION_NOT_APPLIED",
                    "设备当前配置与原清运冻结配置不一致");
        }
        List<Long> active = jdbc.query("""
                        SELECT id
                        FROM rec_port_baseline_measurement
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                          AND status = 'PENDING'
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationId,
                target.portId());
        if (!active.isEmpty()) {
            throw conflict(
                    "DEVICE.PORT_WORK_ACTIVE",
                    "当前投口已有空袋测量正在执行");
        }
    }

    private Map<String, Object> commandEnvelope(
            UUID commandUid,
            Target target,
            UUID measurementUid,
            LocalDateTime now,
            Map<String, Object> payload,
            byte[] payloadSha256) {
        Instant issuedAt = instant(now);
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", TASK_TYPE);
        envelope.put("targetDeviceName", target.hardwareSn());
        envelope.put("target", Map.of(
                "type", TARGET_TYPE,
                "uid", measurementUid.toString()));
        envelope.put("issuedAt", issuedAt.toString());
        envelope.put(
                "expiresAt",
                issuedAt.plusSeconds(COMMAND_VALIDITY_SECONDS).toString());
        envelope.put("payloadSchemaVersion", 2);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(payloadSha256));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        return envelope;
    }

    private long insertCommand(
            UUID commandUid,
            long measurementId,
            Target target,
            String envelopeJson,
            byte[] envelopeSha256,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        return insertAndReturnKey("""
                INSERT INTO dev_device_command (
                    command_uid, tenant_id, organization_id,
                    asset_id, command_type,
                    delivery_session_id, clean_operation_id,
                    config_application_id, fullness_detection_id,
                    baseline_measurement_id,
                    payload_schema_version, semantic_payload,
                    semantic_payload_sha256, physical_state,
                    queued_at, edge_accepted_at,
                    physical_started_at, physical_ended_at,
                    lock_version, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, 'MEASURE_EMPTY_BAG_BASELINE',
                    NULL, NULL, NULL, NULL, ?,
                    2, CAST(? AS JSON), ?, 'QUEUED',
                    ?, NULL, NULL, NULL, 0, ?, ?
                )
                """,
                commandUid.toString(),
                tenantId,
                organizationId,
                target.assetId(),
                measurementId,
                envelopeJson,
                envelopeSha256,
                now,
                now,
                now);
    }

    private UUID registerTask(
            UUID correlationUid,
            UUID commandUid,
            UUID measurementUid,
            long commandId,
            Target target,
            byte[] envelopeSha256) {
        Map<String, Object> snapshot = new LinkedHashMap<>();
        snapshot.put("schemaVersion", 2);
        snapshot.put("commandUid", commandUid.toString());
        snapshot.put("commandType", TASK_TYPE);
        snapshot.put("targetDeviceName", target.hardwareSn());
        snapshot.put("target", Map.of(
                "type", TARGET_TYPE,
                "uid", measurementUid.toString()));
        snapshot.put("payloadSchemaVersion", 2);
        snapshot.put(
                "semanticPayloadSha256",
                canonicalizer.hex(envelopeSha256));
        return taskRegistration.register(
                new ReliableDeviceTaskRegistration(
                        TASK_TYPE,
                        TASK_TYPE + ":"
                                + measurementUid.toString()
                                .toUpperCase(Locale.ROOT),
                        TARGET_TYPE,
                        measurementUid.toString(),
                        taskRefFactory.issue(
                                target.tenantId(),
                                target.organizationId(),
                                target.assetId(),
                                commandId),
                        2,
                        writeJson(snapshot),
                        envelopeSha256,
                        correlationUid,
                        null,
                        12,
                        false,
                        null));
    }

    private static void requireOriginalOperationEnded(
            Target target,
            String faultCode) {
        if (!"ABORTED".equals(target.status())
                || !faultCode.equals(target.endReason())) {
            throw conflict(
                    "CLEAN.RECOVERY_STATE_CHANGED",
                    "原清运未按同一故障原因终止，不能处理袋状态");
        }
    }

    private void releaseDeviceOccupancy(
            Target target,
            boolean allowLegacyMissing,
            long tenantId,
            long organizationId) {
        int deleted = jdbc.update("""
                        DELETE FROM dev_device_occupancy
                        WHERE asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND occupancy_kind = 'CLEAN'
                          AND clean_operation_id = ?
                        """,
                target.assetId(),
                tenantId,
                organizationId,
                target.id());
        int expected = target.offlineOccupancyReleasedAt() == null ? 1 : 0;
        if (deleted != expected
                && !(allowLegacyMissing && deleted == 0)) {
            throw new IllegalStateException(
                    "interrupted clean occupancy changed before recovery");
        }
    }

    private void releaseReservedNewBag(
            Target target,
            boolean allowLegacyMissing,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        int released = jdbc.update("""
                        DELETE FROM rec_bag_current_occupancy
                        WHERE bag_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND occupancy_type = 'CLEAN_RESERVED'
                          AND clean_operation_id = ?
                        """,
                target.newBagId(),
                tenantId,
                organizationId,
                target.id());
        if (released == 0 && allowLegacyMissing) {
            return;
        }
        requireSingle(
                released,
                "release interrupted clean bag reservation");
        requireSingle(jdbc.update("""
                        INSERT INTO rec_bag_occupancy_event (
                            event_uid, tenant_id, organization_id,
                            bag_id, port_id, clean_operation_id,
                            event_type, occurred_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?,
                            'RESERVATION_RELEASED', ?, ?
                        )
                        """,
                UUID.randomUUID().toString(),
                tenantId,
                organizationId,
                target.newBagId(),
                target.portId(),
                target.id(),
                now,
                now),
                "record interrupted clean reservation release");
    }

    private void releaseInterlock(
            Target target,
            long tenantId,
            long organizationId) {
        requireSingle(jdbc.update("""
                        DELETE FROM rec_port_clean_restart_interlock
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND port_id = ?
                          AND source_clean_operation_id = ?
                        """,
                tenantId,
                organizationId,
                target.assetId(),
                target.portId(),
                target.id()),
                "release interrupted clean bag interlock");
    }

    private void markPhotosMissing(
            Target target,
            String faultCode,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        jdbc.update("""
                        UPDATE rec_clean_photo
                        SET status = 'PERMANENTLY_MISSING',
                            linked_at = ?,
                            missing_reason = ?,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND clean_operation_id = ?
                          AND status = 'UPLOAD_PENDING'
                        """,
                now,
                faultCode,
                now,
                tenantId,
                organizationId,
                target.id());
    }

    private void requireExactInterlock(
            Target target,
            long tenantId,
            long organizationId) {
        List<Long> rows = jdbc.query("""
                        SELECT source_clean_operation_id
                        FROM rec_port_clean_restart_interlock
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong(
                        "source_clean_operation_id"),
                tenantId,
                organizationId,
                target.assetId(),
                target.portId());
        if (rows.size() != 1 || rows.getFirst() != target.id()) {
            throw conflict(
                    "CLEAN.RECOVERY_INTERLOCK_CHANGED",
                    "投口清运中断锁已变化，请刷新后重新确认");
        }
    }

    private void requireCurrentBagOccupancy(
            Target target,
            long tenantId,
            long organizationId) {
        List<Long> rows = jdbc.query("""
                        SELECT bag_id
                        FROM rec_bag_current_occupancy
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                          AND occupancy_type = 'PORT_BOUND'
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("bag_id"),
                tenantId,
                organizationId,
                target.portId());
        if (target.oldBagId() == null) {
            if (!rows.isEmpty()) {
                throw conflict(
                        "CLEAN.CURRENT_BAG_CHANGED",
                        "投口当前袋已变化，请刷新后重新确认");
            }
        } else if (rows.size() != 1
                || !Objects.equals(rows.getFirst(), target.oldBagId())) {
            throw conflict(
                    "CLEAN.CURRENT_BAG_CHANGED",
                    "投口当前袋已变化，请刷新后重新确认");
        }
    }

    private void requireReservedNewBag(
            Target target,
            long tenantId,
            long organizationId) {
        List<Long> rows = jdbc.query("""
                        SELECT bag_id
                        FROM rec_bag_current_occupancy
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND bag_id = ?
                          AND occupancy_type = 'CLEAN_RESERVED'
                          AND clean_operation_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("bag_id"),
                tenantId,
                organizationId,
                target.newBagId(),
                target.id());
        if (rows.size() != 1) {
            throw conflict(
                    "CLEAN.RESERVED_BAG_CHANGED",
                    "本次清运预留的新袋已变化，请人工检查");
        }
    }

    private String sourceFaultCode(
            Target target,
            long tenantId,
            long organizationId) {
        String faultCode = target.endReason();
        if (!supportsManualRecoveryFault(faultCode)) {
            throw conflict(
                    "CLEAN.RECOVERY_CAUSE_UNSUPPORTED",
                    "当前清运失败原因不能使用袋状态确认入口处理");
        }
        List<Long> rows = jdbc.query("""
                        SELECT event.id
                        FROM dev_device_command command_row
                        JOIN dev_device_command_event event
                          ON event.command_id = command_row.id
                         AND event.tenant_id = command_row.tenant_id
                         AND event.organization_id =
                             command_row.organization_id
                         AND event.asset_id = command_row.asset_id
                        WHERE command_row.tenant_id = ?
                          AND command_row.organization_id = ?
                          AND command_row.asset_id = ?
                          AND command_row.clean_operation_id = ?
                          AND command_row.command_type =
                              'START_CLEAN_OPERATION'
                          AND event.observation_stage = 'FAILED'
                          AND event.error_code = ?
                        LIMIT 1
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationId,
                target.assetId(),
                target.id(),
                faultCode);
        if (rows.size() != 1) {
            throw conflict(
                    "CLEAN.RECOVERY_EVIDENCE_MISSING",
                    "原清运缺少与终止原因一致的设备故障证据");
        }
        return faultCode;
    }

    static boolean supportsManualRecoveryFault(String faultCode) {
        return SUPPORTED_RECOVERY_FAULTS.contains(faultCode);
    }

    private ExistingRecovery loadExistingRecovery(
            long operationId,
            long tenantId,
            long organizationId) {
        return one("""
                        SELECT recovery.id, recovery.recovery_uid,
                               recovery.actual_bag_id,
                               recovery.decision, recovery.status,
                               recovery.cleaner_organization_user_id,
                               measurement.measurement_uid,
                               task.task_uid
                        FROM rec_clean_bag_recovery recovery
                        LEFT JOIN rec_port_baseline_measurement measurement
                          ON measurement.id = (
                              SELECT latest.id
                              FROM rec_port_baseline_measurement latest
                              WHERE latest.clean_bag_recovery_id = recovery.id
                              ORDER BY latest.started_at DESC, latest.id DESC
                              LIMIT 1
                          )
                        LEFT JOIN dev_device_command command_row
                          ON command_row.baseline_measurement_id = measurement.id
                         AND command_row.tenant_id = recovery.tenant_id
                         AND command_row.organization_id =
                             recovery.organization_id
                         AND command_row.command_type =
                             'MEASURE_EMPTY_BAG_BASELINE'
                        LEFT JOIN ops_reliable_task task
                          ON task.source_device_command_id = command_row.id
                        WHERE recovery.clean_operation_id = ?
                          AND recovery.tenant_id = ?
                          AND recovery.organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ExistingRecovery(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("recovery_uid")),
                        rs.getLong("actual_bag_id"),
                        Decision.valueOf(rs.getString("decision")),
                        rs.getString("status"),
                        rs.getLong("cleaner_organization_user_id"),
                        nullableUuid(rs, "measurement_uid"),
                        nullableUuid(rs, "task_uid")),
                operationId,
                tenantId,
                organizationId).orElse(null);
    }

    private static void requireSameRecovery(
            ExistingRecovery existing,
            Target target,
            Decision decision) {
        long expectedBag = decision == Decision.RETAIN_OLD_BAG
                ? Objects.requireNonNull(target.oldBagId())
                : target.newBagId();
        if (existing.decision() != decision
                || existing.actualBagId() != expectedBag) {
            throw conflict(
                    "CLEAN.RECOVERY_ALREADY_CONFIRMED",
                    "该中断清运已由另一项人工袋确认处理");
        }
    }

    private static Decision decide(Target target, String bagCode) {
        return decide(
                target.oldBagCode(), target.newBagCode(), bagCode);
    }

    static Decision decide(
            String oldBagCode,
            String newBagCode,
            String actualBagCode) {
        if (oldBagCode != null
                && oldBagCode.equals(actualBagCode)) {
            return Decision.RETAIN_OLD_BAG;
        }
        if (newBagCode.equals(actualBagCode)) {
            return Decision.USE_RESERVED_NEW_BAG;
        }
        return null;
    }

    private InterruptedCleanBagRecoveryAccepted response(
            UUID recoveryUid,
            Target target,
            Decision decision,
            String state,
            UUID measurementUid,
            UUID taskUid) {
        String bagCode = decision == Decision.RETAIN_OLD_BAG
                ? target.oldBagCode() : target.newBagCode();
        String nextAction = switch (state) {
            case "COMPLETED" -> "WAIT_FOR_NEXT_BUSINESS";
            case "BASELINE_PENDING" -> "WAIT_FOR_EMPTY_BAG_BASELINE";
            case "BASELINE_REQUIRED" -> "RETRY_EMPTY_BAG_BASELINE";
            default -> throw new IllegalStateException(
                    "unsupported clean bag recovery response state");
        };
        return new InterruptedCleanBagRecoveryAccepted(
                recoveryUid,
                target.operationUid(),
                state,
                decision.name(),
                bagCode,
                measurementUid,
                taskUid,
                nextAction);
    }

    private void appendAudit(
            UUID idempotencyKey,
            String fingerprint,
            InterruptedCleanBagRecoveryAccepted response,
            LockedCleanOrganizationUser cleaner,
            long tenantId,
            long organizationId,
            long organizationUserId,
            UUID operationUid) {
        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("fingerprint", fingerprint);
        summary.put("response", response);
        try {
            cleaner.auditActorRef().withAuditActorOnce(
                    (auditTenantId,
                     auditOrganizationId,
                     auditOrganizationUserId) -> {
                        if (auditTenantId != tenantId
                                || auditOrganizationId
                                    != organizationId
                                || auditOrganizationUserId
                                    != organizationUserId) {
                            throw new IllegalStateException(
                                    "clean recovery audit actor changed");
                        }
                        audit.append(new AuditEntry(
                                UUID.randomUUID(),
                                UUID.randomUUID(),
                                idempotencyKey,
                                AuditScopeKind.ORGANIZATION,
                                tenantId,
                                organizationId,
                                AuditActorKind.ORGANIZATION_USER,
                                null,
                                null,
                                organizationUserId,
                                null,
                                null,
                                ACTION,
                                "CLEAN_OPERATION",
                                operationUid.toString(),
                                "MINIAPP_USER",
                                "SUCCEEDED",
                                cleaner.loginSessionUid().value(),
                                null,
                                writeJson(summary),
                                Instant.now()));
                        return null;
                    });
        } catch (DuplicateKeyException exception) {
            throw idempotencyConflict();
        }
    }

    private InterruptedCleanBagRecoveryAccepted replay(
            SuccessfulAudit previous,
            String fingerprint,
            long tenantId,
            long organizationId,
            long organizationUserId,
            UUID operationUid) {
        JsonNode summary = readJson(previous.safeChangeSummaryJson());
        if (previous.actorKind() != AuditActorKind.ORGANIZATION_USER
                || !Objects.equals(
                        previous.organizationUserId(),
                        organizationUserId)
                || previous.scopeKind()
                    != AuditScopeKind.ORGANIZATION
                || !Objects.equals(previous.tenantId(), tenantId)
                || !Objects.equals(
                        previous.organizationId(), organizationId)
                || !ACTION.equals(previous.actionCode())
                || !"CLEAN_OPERATION".equals(previous.targetType())
                || !operationUid.toString().equals(
                        previous.targetStableKey())
                || !fingerprint.equals(
                        summary.path("fingerprint").asText())) {
            throw idempotencyConflict();
        }
        try {
            return objectMapper.treeToValue(
                    summary.path("response"),
                    InterruptedCleanBagRecoveryAccepted.class);
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private static Target target(ResultSet rs) throws SQLException {
        return new Target(
                rs.getLong("id"),
                UUID.fromString(rs.getString("operation_uid")),
                rs.getLong("asset_id"),
                rs.getLong("port_id"),
                rs.getLong("cleaner_organization_user_id"),
                rs.getInt("port_no"),
                rs.getString("hardware_sn"),
                rs.getString("status"),
                rs.getLong("lock_version"),
                rs.getString("end_reason"),
                nullableLong(rs, "old_bag_id"),
                nullableUuid(rs, "old_bag_uid"),
                rs.getString("old_bag_code"),
                nullableLong(rs, "old_baseline_id"),
                nullableLong(rs, "old_baseline_weight_g"),
                rs.getLong("new_bag_id"),
                UUID.fromString(rs.getString("new_bag_uid")),
                rs.getString("new_bag_code"),
                rs.getObject(
                        "offline_occupancy_released_at",
                        LocalDateTime.class),
                rs.getLong("config_id"),
                rs.getLong("config_version"),
                rs.getString("content_sha256"),
                rs.getString("mcu_payload_sha256"),
                rs.getLong("snapshot_id"),
                rs.getLong("calibration_version"),
                rs.getString("fullness_mode"),
                rs.getLong("configured_full_weight_g"),
                rs.getLong("fullness_settle_wait_ms"),
                rs.getLong("fullness_confirmation_wait_ms"),
                rs.getLong("weight_measurement_timeout_ms"),
                rs.getLong("capacity_version"),
                nullableLong(rs, "current_bag_id"),
                rs.getString("baseline_state"),
                nullableLong(rs, "current_baseline_id"),
                nullableLong(rs, "current_baseline_weight_g"),
                nullableLong(rs, "baseline_bag_id"),
                nullableLong(rs, "baseline_weight_g"),
                nullableLong(rs, "applied_config_version_no"),
                rs.getString("applied_content_sha256"),
                rs.getString("applied_mcu_payload_sha256"),
                rs.getLong("tenant_id"),
                rs.getLong("organization_id"));
    }

    private static byte[] fullnessRuleFingerprint(Target target) {
        String value = String.join(
                "|",
                "FULLNESS_RULE_V1",
                target.fullnessMode(),
                Long.toString(target.configuredFullWeightGrams()),
                Long.toString(target.fullnessSettleWaitMs()),
                Long.toString(target.fullnessConfirmationWaitMs()),
                Long.toString(target.measurementTimeoutMs()));
        return sha256(value);
    }

    private static String fingerprint(
            UUID userUid,
            UUID operationUid,
            String bagCode,
            Boolean emptyBagConfirmed,
            Long expectedVersion,
            String reason) {
        return HexFormat.of().formatHex(sha256(
                userUid + "\u0000" + operationUid + "\u0000"
                        + bagCode + "\u0000" + emptyBagConfirmed
                        + "\u0000" + expectedVersion + "\u0000"
                        + reason));
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(
                    value.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", exception);
        }
    }

    private long insertAndReturnKey(String sql, Object... parameters) {
        KeyHolder keys = new GeneratedKeyHolder();
        PreparedStatementCreator creator = connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    sql, Statement.RETURN_GENERATED_KEYS);
            for (int index = 0; index < parameters.length; index++) {
                statement.setObject(index + 1, parameters[index]);
            }
            return statement;
        };
        requireSingle(jdbc.update(creator, keys), "insert clean recovery fact");
        Number key = keys.getKey();
        if (key == null || key.longValue() <= 0) {
            throw new IllegalStateException(
                    "clean recovery generated key is unavailable");
        }
        return key.longValue();
    }

    private <T> Optional<T> one(
            String sql,
            org.springframework.jdbc.core.RowMapper<T> mapper,
            Object... parameters) {
        return jdbc.query(sql, mapper, parameters)
                .stream().findFirst();
    }

    private void lockSingle(
            String sql,
            String target,
            Object... parameters) {
        List<Long> rows = jdbc.query(
                sql,
                (rs, ignored) -> rs.getLong(1),
                parameters);
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "interrupted clean recovery cannot lock " + target);
        }
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
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "clean recovery JSON cannot be encoded", exception);
        }
    }

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private static void requireSingle(int affected, String operation) {
        if (affected != 1) {
            throw new IllegalStateException(
                    operation + " affected " + affected + " rows");
        }
    }

    private static void requireUuidV4(UUID value, String field) {
        if (value == null || value.version() != 4
                || value.variant() != 2) {
            throw invalid(
                    "COMMON.INVALID_IDEMPOTENCY_KEY",
                    field + " 必须是 UUIDv4");
        }
    }

    private static Long nullableLong(ResultSet rs, String column)
            throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static UUID nullableUuid(ResultSet rs, String column)
            throws SQLException {
        String value = rs.getString(column);
        return value == null ? null : UUID.fromString(value);
    }

    private static Instant instant(LocalDateTime value) {
        return value.toInstant(ZoneOffset.UTC);
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "RESOURCE.NOT_FOUND", "清运操作不存在");
    }

    private static TargetApiException invalid(
            String code, String message) {
        return new TargetApiException(422, code, message);
    }

    private static TargetApiException conflict(
            String code, String message) {
        return new TargetApiException(409, code, message);
    }

    private static TargetApiException idempotencyConflict() {
        return conflict(
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "该 Idempotency-Key 已用于另一项请求");
    }

    enum Decision {
        RETAIN_OLD_BAG,
        USE_RESERVED_NEW_BAG
    }

    private record BaselineAttempt(
            UUID measurementUid,
            UUID taskUid) {
    }

    private record ExistingRecovery(
            long id,
            UUID recoveryUid,
            long actualBagId,
            Decision decision,
            String status,
            long cleanerOrganizationUserId,
            UUID measurementUid,
            UUID taskUid) {
    }

    private record RecoveryCoordinates(
            long assetId,
            long portId) {
    }

    record Target(
            long id,
            UUID operationUid,
            long assetId,
            long portId,
            long cleanerOrganizationUserId,
            int portNo,
            String hardwareSn,
            String status,
            long lockVersion,
            String endReason,
            Long oldBagId,
            UUID oldBagUid,
            String oldBagCode,
            Long oldBaselineId,
            Long oldBaselineWeightGrams,
            long newBagId,
            UUID newBagUid,
            String newBagCode,
            LocalDateTime offlineOccupancyReleasedAt,
            long configurationId,
            long configurationVersion,
            String contentSha256,
            String mcuPayloadSha256,
            long snapshotId,
            long calibrationVersion,
            String fullnessMode,
            long configuredFullWeightGrams,
            long fullnessSettleWaitMs,
            long fullnessConfirmationWaitMs,
            long measurementTimeoutMs,
            long capacityVersion,
            Long currentBagId,
            String baselineState,
            Long currentBaselineId,
            Long currentBaselineWeightGrams,
            Long baselineBagId,
            Long baselineWeightGrams,
            Long appliedConfigurationVersion,
            String appliedContentSha256,
            String appliedMcuPayloadSha256,
            long tenantId,
            long organizationId) {
    }
}
