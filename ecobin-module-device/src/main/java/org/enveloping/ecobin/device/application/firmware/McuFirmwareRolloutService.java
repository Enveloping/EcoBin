package org.enveloping.ecobin.device.application.firmware;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedPlatformDeviceAssetFactEvent;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.CreateRolloutRequest;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.DeploymentView;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.PageData;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.RegisterReleaseRequest;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.ReleaseView;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.RolloutActionRequest;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.RolloutView;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWake;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWakePort;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.DeviceScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeviceScope;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;

/**
 * Platform-owned MCU firmware release and manual rollout state machine.
 *
 * <p>A release is immutable once registered.  A rollout never advances by a
 * scheduler: an operator must start the one-device validation, explicitly
 * promote a successful validation, and explicitly dispatch every later wave.
 * The reliable command stores no COS secret; the OneNet Adapter attaches a
 * fresh read-only grant at each actual transport attempt.</p>
 */
@Service
public class McuFirmwareRolloutService {

    public static final String EVENT_TYPE = "MCU_FIRMWARE_UPDATE_PROGRESS";
    private static final String COMMAND_TYPE = "START_MCU_FIRMWARE_UPDATE";
    private static final String TARGET_TYPE = "MCU_FIRMWARE_DEPLOYMENT";
    private static final String HARDWARE_COMPATIBILITY =
            "ECOBIN_MAINBOARD_V1.1";
    private static final String UUID_V4 =
            "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$";
    private static final String SHA256 = "^[0-9a-f]{64}$";
    private static final String IDENTITY = "^[0-9a-f]{16}$";
    private static final Set<String> TERMINAL = Set.of(
            "SUCCEEDED", "ROLLED_BACK", "FAILED_LOCKED", "REJECTED", "LOCAL_CANCELLED");
    private static final Set<String> RUNNING = Set.of(
            "QUEUED", "PACKAGE_FETCH_FAILED", "PREFLIGHT",
            "PREPARED", "FLASHING_TARGET",
            "VERIFYING_TARGET", "ROLLING_BACK", "VERIFYING_ROLLBACK");
    private static final Set<String> TARGET_ATTEMPT_STAGES = Set.of(
            "FLASHING_TARGET", "VERIFYING_TARGET", "SUCCEEDED");
    private static final Set<String> ROLLBACK_ATTEMPT_STAGES = Set.of(
            "ROLLING_BACK", "VERIFYING_ROLLBACK", "ROLLED_BACK");
    private static final Map<String, Integer> STAGE_ORDER = Map.ofEntries(
            Map.entry("QUEUED", 0),
            Map.entry("PACKAGE_FETCH_FAILED", 1),
            Map.entry("PREFLIGHT", 2),
            Map.entry("PREPARED", 3),
            Map.entry("FLASHING_TARGET", 4),
            Map.entry("VERIFYING_TARGET", 5),
            Map.entry("SUCCEEDED", 6),
            Map.entry("REJECTED", 6),
            Map.entry("ROLLING_BACK", 7),
            Map.entry("VERIFYING_ROLLBACK", 8),
            Map.entry("ROLLED_BACK", 9),
            Map.entry("FAILED_LOCKED", 10));

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeviceScopeAuthorizationPort authorization;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final PlatformDeviceAssetTaskRefFactory taskRefFactory;
    private final ReliablePlatformDeviceControlTaskRegistrationPort tasks;
    private final ReliableDeviceTaskProofPort taskProof;
    private final ReliableTaskWakePort taskWake;

    public McuFirmwareRolloutService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeviceScopeAuthorizationPort authorization,
            DeviceConfigurationCanonicalizer canonicalizer,
            PlatformDeviceAssetTaskRefFactory taskRefFactory,
            ReliablePlatformDeviceControlTaskRegistrationPort tasks,
            ReliableDeviceTaskProofPort taskProof,
            ReliableTaskWakePort taskWake) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.authorization = authorization;
        this.canonicalizer = canonicalizer;
        this.taskRefFactory = taskRefFactory;
        this.tasks = tasks;
        this.taskProof = taskProof;
        this.taskWake = taskWake;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public ReleaseView registerRelease(
            UUID operationUid,
            RegisterReleaseRequest request) {
        requireUuidV4(operationUid, "Idempotency-Key");
        if (request == null) {
            throw invalid("固件发布信息不能为空");
        }
        requireUuidV4(request.releaseUid(), "releaseUid");
        AuthorizedDeviceScope actor = authorizePlatform();
        TargetWebAuditRequestContext.describe(
                "device.mcu-firmware.release.register",
                request.releaseUid().toString());

        ReleaseRow replay = releaseByOperation(operationUid, false);
        if (replay != null) {
            requireReleaseReplay(replay, request);
            return releaseView(replay);
        }
        if (!HARDWARE_COMPATIBILITY.equals(
                request.hardwareCompatibility())
                || request.fixedFrameRevision() != 2) {
            throw invalid(
                    "当前只接受主板 ECOBIN_MAINBOARD_V1.1、MCU STM32F103C8T6"
                            + " 且固定帧协议修订号为 2 的固件");
        }
        String expectedObjectKey = "ecobin/mcu-firmware/"
                + request.releaseUid()
                + "/"
                + request.packageSha256()
                + ".efw";
        if (!expectedObjectKey.equals(request.objectKey())) {
            throw invalid("COS 对象必须位于该发布专属目录，且文件名必须等于包摘要");
        }
        long adminId = platformAdminId(actor);
        LocalDateTime now = databaseNow();
        try {
            jdbc.update("""
                            INSERT INTO dev_mcu_firmware_release (
                                release_uid, operation_uid,
                                firmware_version, firmware_version_code,
                                firmware_identity_hex,
                                hardware_compatibility, fixed_frame_revision,
                                package_object_key, package_sha256,
                                package_size, release_status, release_notes,
                                created_by_platform_admin_id,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                      'READY', ?, ?, ?, ?)
                            """,
                    request.releaseUid().toString(),
                    operationUid.toString(),
                    request.firmwareVersion(),
                    request.firmwareVersionCode(),
                    request.firmwareIdentityHex(),
                    request.hardwareCompatibility(),
                    request.fixedFrameRevision(),
                    request.objectKey(),
                    HexFormat.of().parseHex(request.packageSha256()),
                    request.packageSize(),
                    nullableTrimmed(request.releaseNotes()),
                    adminId,
                    now,
                    now);
        } catch (DuplicateKeyException exception) {
            throw conflict(
                    "DEVICE.MCU_FIRMWARE_RELEASE_CONFLICT",
                    "该固件包、版本身份或发布编号已经登记");
        }
        return releaseView(requireRelease(request.releaseUid(), false));
    }

    @Transactional(readOnly = true)
    public PageData<ReleaseView> listReleases(int page, int pageSize) {
        authorizePlatform();
        int safePage = page(page);
        int safeSize = pageSize(pageSize);
        long total = jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_mcu_firmware_release",
                Long.class);
        List<ReleaseView> items = jdbc.query(
                RELEASE_SELECT + " ORDER BY firmware_release.created_at DESC,"
                        + " firmware_release.id DESC LIMIT ? OFFSET ?",
                (rs, ignored) -> releaseView(releaseRow(rs)),
                safeSize,
                (safePage - 1) * safeSize);
        return new PageData<>(items, safePage, safeSize, total);
    }

    @Transactional(readOnly = true)
    public ReleaseView releaseDetail(UUID releaseUid) {
        authorizePlatform();
        requireUuidV4(releaseUid, "releaseUid");
        return releaseView(requireRelease(releaseUid, false));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public RolloutView createRollout(
            UUID operationUid,
            CreateRolloutRequest request) {
        requireUuidV4(operationUid, "Idempotency-Key");
        if (request == null) {
            throw invalid("灰度计划不能为空");
        }
        if (request.targetHardwareSns() == null
                || request.targetHardwareSns().isEmpty()
                || request.targetHardwareSns().size() > 1000
                || request.batchSize() == null
                || request.batchSize() < 1
                || request.batchSize() > 100
                || request.reason() == null
                || request.reason().isBlank()
                || request.reason().trim().length() > 500) {
            throw invalid("灰度目标、批次大小或变更原因不正确");
        }
        requireUuidV4(request.releaseUid(), "releaseUid");
        AuthorizedDeviceScope actor = authorizePlatform();
        TargetWebAuditRequestContext.describe(
                "device.mcu-firmware.rollout.create",
                operationUid.toString());
        RolloutRow replay = rolloutByOperation(operationUid, false);
        if (replay != null) {
            requireRolloutReplay(replay, request);
            return rolloutView(replay, true);
        }
        ReleaseRow release = requireRelease(request.releaseUid(), true);
        if (!Set.of("READY", "PROMOTED").contains(release.status())) {
            throw conflict(
                    "DEVICE.MCU_FIRMWARE_RELEASE_UNAVAILABLE",
                    "该固件发布不能再创建灰度计划");
        }
        String validationHardwareSn = hardwareSn(
                request.validationHardwareSn());
        LinkedHashSet<String> targetSet = new LinkedHashSet<>();
        for (String candidate : request.targetHardwareSns()) {
            String normalized = hardwareSn(candidate);
            if (!normalized.equals(validationHardwareSn)) {
                targetSet.add(normalized);
            }
        }
        if (targetSet.isEmpty()) {
            throw invalid("除单设备验证机外，至少还要选择一台灰度目标设备");
        }
        List<String> assetHardwareSns = new ArrayList<>();
        assetHardwareSns.add(validationHardwareSn);
        assetHardwareSns.addAll(targetSet);
        lockAssetsForRollout(assetHardwareSns);
        Asset validation = requireEligibleAsset(validationHardwareSn, false);
        List<Asset> targets = new ArrayList<>();
        for (String target : targetSet) {
            targets.add(requireEligibleAsset(target, false));
        }
        List<Asset> all = new ArrayList<>();
        all.add(validation);
        all.addAll(targets);
        requireNoActiveDeployments(all);

        int maximumWaveNo = (targets.size() + request.batchSize() - 1)
                / request.batchSize();
        long adminId = platformAdminId(actor);
        LocalDateTime now = databaseNow();
        try {
            jdbc.update("""
                            INSERT INTO dev_mcu_firmware_rollout (
                                rollout_uid, operation_uid, release_id,
                                rollout_status, batch_size, maximum_wave_no,
                                current_wave_no, validation_asset_id,
                                change_reason, created_by_platform_admin_id,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, 'DRAFT', ?, ?, -1, ?, ?, ?, ?, ?)
                            """,
                    operationUid.toString(),
                    operationUid.toString(),
                    release.id(),
                    request.batchSize(),
                    maximumWaveNo,
                    validation.id(),
                    request.reason().trim(),
                    adminId,
                    now,
                    now);
        } catch (DuplicateKeyException exception) {
            throw conflict(
                    "DEVICE.MCU_FIRMWARE_ROLLOUT_CONFLICT",
                    "同一操作已经创建过灰度计划");
        }
        RolloutRow rollout = requireRollout(operationUid, true);
        insertDeployment(rollout, release, validation, "VALIDATION", 0, now);
        for (int index = 0; index < targets.size(); index++) {
            int waveNo = index / request.batchSize() + 1;
            insertDeployment(
                    rollout, release, targets.get(index), "WAVE", waveNo, now);
        }
        return rolloutView(requireRollout(operationUid, false), true);
    }

    @Transactional(readOnly = true)
    public PageData<RolloutView> listRollouts(int page, int pageSize) {
        authorizePlatform();
        int safePage = page(page);
        int safeSize = pageSize(pageSize);
        long total = jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_mcu_firmware_rollout",
                Long.class);
        List<RolloutRow> rows = jdbc.query(
                ROLLOUT_SELECT + " ORDER BY rollout.created_at DESC,"
                        + " rollout.id DESC LIMIT ? OFFSET ?",
                (rs, ignored) -> rolloutRow(rs),
                safeSize,
                (safePage - 1) * safeSize);
        return new PageData<>(
                rows.stream().map(row -> rolloutView(row, false)).toList(),
                safePage,
                safeSize,
                total);
    }

    @Transactional(readOnly = true)
    public RolloutView detail(UUID rolloutUid) {
        authorizePlatform();
        requireUuidV4(rolloutUid, "rolloutUid");
        return rolloutView(requireRollout(rolloutUid, false), true);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public RolloutView startValidation(
            UUID operationUid,
            UUID rolloutUid,
            RolloutActionRequest request) {
        return executeAction(
                operationUid, rolloutUid, request, "START_VALIDATION",
                (rollout, adminId, reason, now) -> {
                    if (!"DRAFT".equals(rollout.status())) {
                        throw conflict(
                                "DEVICE.MCU_FIRMWARE_VALIDATION_STATE",
                                "只有尚未开始的灰度计划可以启动单设备验证");
                    }
                    Deployment deployment = deployments(
                            rollout.id(), true).stream()
                            .filter(item -> "VALIDATION".equals(item.kind()))
                            .findFirst()
                            .orElseThrow(() -> new IllegalStateException(
                                    "firmware rollout lacks validation deployment"));
                    dispatch(rollout, deployment, reason, now);
                    jdbc.update("""
                                    UPDATE dev_mcu_firmware_rollout
                                    SET rollout_status = 'VALIDATING',
                                        current_wave_no = 0,
                                        lock_version = lock_version + 1,
                                        updated_at = ?
                                    WHERE id = ?
                                    """,
                            now,
                            rollout.id());
                    return 0;
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public RolloutView promote(
            UUID operationUid,
            UUID rolloutUid,
            RolloutActionRequest request) {
        return executeAction(
                operationUid, rolloutUid, request, "PROMOTE",
                (rollout, adminId, reason, now) -> {
                    if (!"AWAITING_PROMOTION".equals(rollout.status())) {
                        throw conflict(
                                "DEVICE.MCU_FIRMWARE_PROMOTION_STATE",
                                "只有单设备验证成功后才能人工确认推广");
                    }
                    jdbc.update("""
                                    UPDATE dev_mcu_firmware_release
                                    SET release_status = 'PROMOTED',
                                        promoted_by_platform_admin_id =
                                            COALESCE(promoted_by_platform_admin_id, ?),
                                        promoted_at = COALESCE(promoted_at, ?),
                                        updated_at = ?
                                    WHERE id = ?
                                      AND release_status IN ('READY', 'PROMOTED')
                                    """,
                            adminId,
                            now,
                            now,
                            rollout.releaseId());
                    jdbc.update("""
                                    UPDATE dev_mcu_firmware_rollout
                                    SET rollout_status = 'ACTIVE',
                                        promoted_by_platform_admin_id = ?,
                                        promoted_at = ?,
                                        lock_version = lock_version + 1,
                                        updated_at = ?
                                    WHERE id = ?
                                    """,
                            adminId,
                            now,
                            now,
                            rollout.id());
                    return 0;
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public RolloutView advanceWave(
            UUID operationUid,
            UUID rolloutUid,
            RolloutActionRequest request) {
        return executeAction(
                operationUid, rolloutUid, request, "ADVANCE_WAVE",
                (rollout, adminId, reason, now) -> {
                    if (!"ACTIVE".equals(rollout.status())) {
                        throw conflict(
                                "DEVICE.MCU_FIRMWARE_ROLLOUT_STATE",
                                "只有已人工推广且未结束的计划可以下发下一批");
                    }
                    if (rollout.currentWaveNo() > 0) {
                        WaveState current = waveState(
                                rollout.id(), rollout.currentWaveNo());
                        if (current.failed() > 0) {
                            throw conflict(
                                    "DEVICE.MCU_FIRMWARE_WAVE_FAILED",
                                    "当前批次存在回滚或失败设备，必须人工处置，系统不会自动跳批");
                        }
                        if (current.total() == 0
                                || current.succeeded() != current.total()) {
                            throw conflict(
                                    "DEVICE.MCU_FIRMWARE_WAVE_RUNNING",
                                    "当前批次尚未全部升级成功，不能下发下一批");
                        }
                    }
                    int nextWave = rollout.currentWaveNo() + 1;
                    if (nextWave > rollout.maximumWaveNo()) {
                        jdbc.update("""
                                        UPDATE dev_mcu_firmware_rollout
                                        SET rollout_status = 'COMPLETED',
                                            lock_version = lock_version + 1,
                                            updated_at = ?
                                        WHERE id = ?
                                        """,
                                now,
                                rollout.id());
                        return null;
                    }
                    List<Deployment> wave = deployments(
                            rollout.id(), true).stream()
                            .filter(item -> item.waveNo() == nextWave)
                            .toList();
                    if (wave.isEmpty()
                            || wave.stream().anyMatch(item ->
                            !"PENDING".equals(item.status()))) {
                        throw new IllegalStateException(
                                "firmware rollout wave is not pending");
                    }
                    // Resolve every dispatch gate first so a wave is all-or-none.
                    for (Deployment deployment : wave) {
                        requireEligibleAsset(deployment.hardwareSn(), true);
                    }
                    for (Deployment deployment : wave) {
                        dispatch(rollout, deployment, reason, now);
                    }
                    jdbc.update("""
                                    UPDATE dev_mcu_firmware_rollout
                                    SET current_wave_no = ?,
                                        lock_version = lock_version + 1,
                                        updated_at = ?
                                    WHERE id = ?
                                    """,
                            nextWave,
                            now,
                            rollout.id());
                    return nextWave;
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public RolloutView stop(
            UUID operationUid,
            UUID rolloutUid,
            RolloutActionRequest request) {
        return executeAction(
                operationUid, rolloutUid, request, "STOP",
                (rollout, adminId, reason, now) -> {
                    if (!"ACTIVE".equals(rollout.status())) {
                        throw conflict(
                                "DEVICE.MCU_FIRMWARE_STOP_STATE",
                                "只有已推广且仍在进行的计划可以停止");
                    }
                    if (deployments(rollout.id(), true).stream()
                            .anyMatch(item -> RUNNING.contains(item.status()))) {
                        throw conflict(
                                "DEVICE.MCU_FIRMWARE_UPDATE_RUNNING",
                                "仍有设备正在升级；待其成功、回滚或失败锁定后再停止计划");
                    }
                    jdbc.update("""
                                    UPDATE dev_mcu_firmware_rollout
                                    SET rollout_status = 'STOPPED',
                                        stopped_by_platform_admin_id = ?,
                                        stopped_at = ?, stop_reason = ?,
                                        lock_version = lock_version + 1,
                                        updated_at = ?
                                    WHERE id = ?
                                    """,
                            adminId,
                            now,
                            reason,
                            now,
                            rollout.id());
                    return rollout.currentWaveNo();
                });
    }

    /** Applies one authenticated, platform-scoped progress fact. */
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public TrustedDeviceEventApplyResult applyProgress(
            TrustedPlatformDeviceAssetFactEvent inboxEvent) {
        if (!EVENT_TYPE.equals(inboxEvent.messageKind())) {
            throw new IllegalArgumentException(
                    "unsupported MCU firmware progress event");
        }
        return inboxEvent.sourceInbox().use(sourceInboxId -> {
            JsonNode normalized = objectMapper.readTree(
                    inboxEvent.normalizedPayload());
            JsonNode source = requiredObject(normalized, "trustedSource");
            JsonNode event = requiredObject(normalized, "event");
            JsonNode target = requiredObject(event, "target");
            JsonNode payload = requiredObject(event, "payload");
            requireText(event, "eventType", EVENT_TYPE);
            requireText(target, "type", TARGET_TYPE);
            UUID eventUid = uuid(event, "eventUid");
            if (progressExists(eventUid, sourceInboxId)) {
                return TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
            }
            UUID deploymentUid = uuid(payload, "deploymentUid");
            if (!deploymentUid.equals(uuid(target, "uid"))) {
                throw new IllegalArgumentException(
                        "firmware progress target differs from deployment");
            }
            Deployment deployment = requireDeployment(deploymentUid, true);
            ReleaseRow release = releaseById(deployment.releaseId(), true);
            String sourceHardwareSn = text(source, "deviceName", 64);
            if (!sourceHardwareSn.equals(deployment.hardwareSn())) {
                throw new IllegalArgumentException(
                        "firmware progress source differs from deployment asset");
            }
            if (!uuid(payload, "releaseUid").equals(release.uid())
                    || !release.version().equals(
                    text(payload, "firmwareVersion", 32))
                    || release.versionCode()
                    != integer(payload, "firmwareVersionCode", 1,
                    4_294_967_295L)
                    || !release.identityHex().equals(
                    pattern(payload, "firmwareIdentityHex", IDENTITY, 16))
                    || integer(payload, "fixedFrameRevision", 2, 2) != 2
                    || !"CLOUD".equals(text(payload, "source", 16))
                    || bool(payload, "legacyPreflight")
                    || bool(payload, "downgradeAuthorized")) {
                throw new IllegalArgumentException(
                        "firmware progress differs from frozen release identity");
            }
            UUID commandUid = nullableUuid(event, "commandUid");
            if (commandUid == null
                    || !commandUid.equals(deployment.commandUid())) {
                throw new IllegalArgumentException(
                        "firmware progress command differs from deployment");
            }
            String stage = stage(payload);
            int targetAttempts = Math.toIntExact(integer(
                    payload, "targetAttemptCount", 0, 3));
            int rollbackAttempts = Math.toIntExact(integer(
                    payload, "rollbackAttemptCount", 0, 3));
            String errorCode = nullablePattern(
                    payload, "errorCode", "^[A-Z][A-Z0-9_]{0,63}$", 64);
            if (Set.of(
                    "PACKAGE_FETCH_FAILED",
                    "FAILED_LOCKED",
                    "REJECTED").contains(stage)
                    != (errorCode != null)) {
                throw new IllegalArgumentException(
                        "firmware progress failure code differs from stage");
            }
            Installed installed = installed(payload);
            if ("SUCCEEDED".equals(stage)
                    && (installed == null
                    || !release.version().equals(installed.version())
                    || release.versionCode() != installed.versionCode()
                    || !release.identityHex().equals(installed.identityHex()))) {
                throw new IllegalArgumentException(
                        "successful firmware progress does not prove target identity");
            }
            UUID updateUid = uuid(payload, "updateUid");
            if (deployment.edgeUpdateUid() != null
                    && !deployment.edgeUpdateUid().equals(updateUid)) {
                throw new IllegalArgumentException(
                        "firmware progress update identity changed");
            }
            boolean advancesProjection = progressAdvances(
                    deployment,
                    stage,
                    targetAttempts,
                    rollbackAttempts);
            String payloadSha256 = pattern(
                    event, "payloadSha256", SHA256, 64);
            LocalDateTime now = databaseNow();
            LocalDateTime occurredAt = nullableInstant(event, "occurredAt");
            try {
                jdbc.update("""
                                INSERT INTO dev_mcu_firmware_progress (
                                    event_uid, source_inbox_id, deployment_id,
                                    edge_update_uid, stage,
                                    target_attempt_count,
                                    rollback_attempt_count, error_code,
                                    payload_sha256, normalized_payload,
                                    occurred_at, received_at, created_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                        eventUid.toString(),
                        sourceInboxId,
                        deployment.id(),
                        updateUid.toString(),
                        stage,
                        targetAttempts,
                        rollbackAttempts,
                        errorCode,
                        HexFormat.of().parseHex(payloadSha256),
                        inboxEvent.normalizedPayload(),
                        occurredAt,
                        now,
                        now);
            } catch (DuplicateKeyException duplicate) {
                return TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
            }
            if (TERMINAL.contains(deployment.status())) {
                return TrustedDeviceEventApplyResult.APPLIED;
            }
            if (!advancesProjection) {
                if ("PACKAGE_FETCH_FAILED".equals(stage)) {
                    taskWake.wake(new ReliableTaskWake(
                            deployment.taskUid(),
                            "MCU_FIRMWARE_PACKAGE_FETCH_FAILED"));
                }
                return TrustedDeviceEventApplyResult.APPLIED;
            }
            LocalDateTime completedAt = TERMINAL.contains(stage) ? now : null;
            int updated = jdbc.update("""
                            UPDATE dev_mcu_firmware_deployment
                            SET deployment_status = ?, edge_update_uid = ?,
                                target_attempt_count = ?,
                                rollback_attempt_count = ?,
                                installed_firmware_version = ?,
                                installed_firmware_version_code = ?,
                                installed_firmware_identity_hex = ?,
                                error_code = ?, last_event_uid = ?,
                                completed_at = ?,
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE id = ?
                              AND deployment_status NOT IN (
                                  'SUCCEEDED', 'ROLLED_BACK',
                                  'FAILED_LOCKED', 'REJECTED', 'LOCAL_CANCELLED'
                              )
                            """,
                    stage,
                    updateUid.toString(),
                    targetAttempts,
                    rollbackAttempts,
                    installed == null ? null : installed.version(),
                    installed == null ? null : installed.versionCode(),
                    installed == null ? null : installed.identityHex(),
                    errorCode,
                    eventUid.toString(),
                    completedAt,
                    now,
                    deployment.id());
            if (updated != 1) {
                throw new IllegalStateException(
                        "firmware deployment update lost its lock");
            }
            if ("PACKAGE_FETCH_FAILED".equals(stage)) {
                taskWake.wake(new ReliableTaskWake(
                        deployment.taskUid(),
                        "MCU_FIRMWARE_PACKAGE_FETCH_FAILED"));
            } else {
                taskProof.completeFromTrustedProof(
                        COMMAND_TYPE,
                        TARGET_TYPE,
                        deployment.uid().toString());
            }
            if (installed != null && TERMINAL.contains(stage)) {
                jdbc.update("""
                                UPDATE dev_device_asset
                                SET mcu_firmware_version_code = ?,
                                    mcu_firmware_identity_hex = ?,
                                    mcu_fixed_frame_revision = 2,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                        installed.versionCode(),
                        installed.identityHex(),
                        now,
                        deployment.assetId());
                jdbc.update("""
                                UPDATE dev_device_runtime_state
                                SET mcu_firmware_version = ?,
                                    lock_version = lock_version + 1,
                                    updated_at = ?
                                WHERE asset_id = ?
                                """,
                        installed.version(),
                        now,
                        deployment.assetId());
            }
            if ("VALIDATION".equals(deployment.kind())
                    && "VALIDATING".equals(deployment.rolloutStatus())
                    && TERMINAL.contains(stage)) {
                jdbc.update("""
                                UPDATE dev_mcu_firmware_rollout
                                SET rollout_status = ?,
                                    lock_version = lock_version + 1,
                                    updated_at = ?
                                WHERE id = ? AND rollout_status = 'VALIDATING'
                                """,
                        "SUCCEEDED".equals(stage)
                                ? "AWAITING_PROMOTION"
                                : "VALIDATION_FAILED",
                        now,
                        deployment.rolloutId());
            }
            return TrustedDeviceEventApplyResult.APPLIED;
        });
    }

    private RolloutView executeAction(
            UUID operationUid,
            UUID rolloutUid,
            RolloutActionRequest request,
            String action,
            ActionMutation mutation) {
        requireUuidV4(operationUid, "Idempotency-Key");
        requireUuidV4(rolloutUid, "rolloutUid");
        if (request == null
                || request.reason() == null
                || request.reason().isBlank()) {
            throw invalid("操作原因不能为空");
        }
        AuthorizedDeviceScope actor = authorizePlatform();
        TargetWebAuditRequestContext.describe(
                "device.mcu-firmware.rollout."
                        + action.toLowerCase(Locale.ROOT),
                rolloutUid.toString());
        ActionReplay replay = actionReplay(operationUid);
        if (replay != null) {
            if (!replay.rolloutUid().equals(rolloutUid)
                    || !replay.action().equals(action)
                    || !replay.reason().equals(request.reason().trim())) {
                throw conflict(
                        "DEVICE.IDEMPOTENCY_CONFLICT",
                        "同一幂等键已经用于另一项固件灰度操作");
            }
            return rolloutView(requireRollout(rolloutUid, false), true);
        }
        RolloutRow rollout = requireRollout(rolloutUid, true);
        long adminId = platformAdminId(actor);
        String reason = request.reason().trim();
        LocalDateTime now = databaseNow();
        Integer resultingWave = mutation.apply(
                rollout, adminId, reason, now);
        try {
            jdbc.update("""
                            INSERT INTO dev_mcu_firmware_rollout_action (
                                operation_uid, rollout_id, action_type,
                                resulting_wave_no,
                                requested_by_platform_admin_id,
                                reason, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                    operationUid.toString(),
                    rollout.id(),
                    action,
                    resultingWave,
                    adminId,
                    reason,
                    now);
        } catch (DuplicateKeyException exception) {
            throw conflict(
                    "DEVICE.IDEMPOTENCY_CONFLICT",
                    "同一幂等键已经用于另一项固件灰度操作");
        }
        return rolloutView(requireRollout(rolloutUid, false), true);
    }

    private void dispatch(
            RolloutRow rollout,
            Deployment deployment,
            String reason,
            LocalDateTime now) {
        if (!"PENDING".equals(deployment.status())) {
            throw new IllegalStateException(
                    "only a pending firmware deployment can be dispatched");
        }
        Asset asset = requireEligibleAsset(deployment.hardwareSn(), true);
        ReleaseRow release = releaseById(deployment.releaseId(), true);
        UUID commandUid = UUID.randomUUID();
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("deploymentUid", deployment.uid().toString());
        payload.put("releaseUid", release.uid().toString());
        payload.put("firmwareVersion", release.version());
        payload.put("firmwareVersionCode", release.versionCode());
        payload.put("firmwareIdentityHex", release.identityHex());
        payload.put("objectKey", release.objectKey());
        payload.put(
                "packageSha256",
                HexFormat.of().formatHex(release.packageSha256()));
        payload.put("packageSize", release.packageSize());
        payload.put("reason", nullableTrimmed(reason));

        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", COMMAND_TYPE);
        envelope.put("targetDeviceName", asset.hardwareSn());
        envelope.put("target", Map.of(
                "type", TARGET_TYPE,
                "uid", deployment.uid().toString()));
        envelope.put("issuedAt", timestamp(now));
        envelope.put("expiresAt", timestamp(now.plusMinutes(15)));
        envelope.put("payloadSchemaVersion", 2);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(canonicalizer.payloadSha256(payload)));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        byte[] envelopeSha256 = canonicalizer.payloadSha256(envelope);
        UUID taskUid = tasks.register(
                new ReliablePlatformDeviceControlTaskRegistration(
                        COMMAND_TYPE,
                        COMMAND_TYPE + ":"
                                + deployment.uid().toString()
                                .toUpperCase(Locale.ROOT),
                        TARGET_TYPE,
                        deployment.uid().toString(),
                        taskRefFactory.issue(asset.id()),
                        2,
                        objectMapper.writeValueAsString(envelope),
                        envelopeSha256,
                        rollout.uid(),
                        commandUid,
                        20));
        int updated = jdbc.update("""
                        UPDATE dev_mcu_firmware_deployment
                        SET deployment_status = 'QUEUED',
                            command_uid = ?, reliable_task_uid = ?,
                            queued_at = ?, updated_at = ?,
                            lock_version = lock_version + 1
                        WHERE id = ? AND deployment_status = 'PENDING'
                        """,
                commandUid.toString(),
                taskUid.toString(),
                now,
                now,
                deployment.id());
        if (updated != 1) {
            throw new IllegalStateException(
                    "firmware deployment dispatch lost its lock");
        }
    }

    private void insertDeployment(
            RolloutRow rollout,
            ReleaseRow release,
            Asset asset,
            String kind,
            int waveNo,
            LocalDateTime now) {
        jdbc.update("""
                        INSERT INTO dev_mcu_firmware_deployment (
                            deployment_uid, rollout_id, release_id,
                            asset_id, tenant_id, organization_id,
                            deployment_kind, wave_no, deployment_status,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
                        """,
                UUID.randomUUID().toString(),
                rollout.id(),
                release.id(),
                asset.id(),
                asset.tenantId(),
                asset.organizationId(),
                kind,
                waveNo,
                now,
                now);
    }

    private void requireNoActiveDeployments(List<Asset> assets) {
        for (Asset asset : assets) {
            long count = jdbc.queryForObject("""
                            SELECT COUNT(*)
                            FROM dev_mcu_firmware_deployment deployment
                            JOIN dev_mcu_firmware_rollout rollout
                              ON rollout.id = deployment.rollout_id
                            WHERE deployment.asset_id = ?
                              AND deployment.deployment_status <> 'LOCAL_CANCELLED'
                              AND rollout.rollout_status IN (
                                  'DRAFT', 'VALIDATING',
                                  'AWAITING_PROMOTION', 'ACTIVE'
                              )
                            """,
                    Long.class,
                    asset.id());
            if (count > 0) {
                throw conflict(
                        "DEVICE.MCU_FIRMWARE_DEVICE_BUSY",
                        "设备 " + asset.hardwareSn()
                                + " 已属于另一项未结束的固件灰度计划");
            }
        }
    }

    private void lockAssetsForRollout(List<String> hardwareSns) {
        for (String hardwareSn : hardwareSns.stream().sorted().toList()) {
            List<Long> rows = jdbc.query(
                    "SELECT id FROM dev_device_asset"
                            + " WHERE hardware_sn = ? FOR UPDATE",
                    (rs, ignored) -> rs.getLong("id"),
                    hardwareSn);
            if (rows.size() != 1) {
                throw notFound("目标设备不存在");
            }
        }
    }

    private Asset requireEligibleAsset(
            String hardwareSn,
            boolean requireOnline) {
        List<Asset> rows = jdbc.query("""
                        SELECT asset.id, asset.hardware_sn,
                               asset.tenant_id, asset.organization_id,
                               asset.lifecycle_status,
                               asset.acceptance_status,
                               asset.mcu_fixed_frame_revision,
                               asset.mcu_remote_update_capable,
                               COALESCE(transport.onenet_connection_status,
                                        'UNKNOWN') AS transport_status
                        FROM dev_device_asset asset
                        LEFT JOIN dev_device_transport_state transport
                          ON transport.asset_id = asset.id
                        WHERE asset.hardware_sn = ?
                        """,
                (rs, ignored) -> new Asset(
                        rs.getLong("id"),
                        rs.getString("hardware_sn"),
                        rs.getObject("tenant_id", Long.class),
                        rs.getObject("organization_id", Long.class),
                        rs.getString("lifecycle_status"),
                        rs.getString("acceptance_status"),
                        rs.getObject("mcu_fixed_frame_revision", Integer.class),
                        rs.getObject(
                                "mcu_remote_update_capable", Boolean.class),
                        rs.getString("transport_status")),
                hardwareSn);
        if (rows.size() != 1) {
            throw notFound("目标设备不存在");
        }
        Asset asset = rows.getFirst();
        if (!"NORMAL".equals(asset.lifecycleStatus())
                || !"PASSED".equals(asset.acceptanceStatus())) {
            throw conflict(
                    "DEVICE.MCU_FIRMWARE_ASSET_UNAVAILABLE",
                    "设备 " + hardwareSn + " 未通过验收或已被禁用/报废");
        }
        if (!Boolean.TRUE.equals(asset.remoteUpdateCapable())) {
            throw conflict(
                    "DEVICE.MCU_REMOTE_UPDATE_UNAVAILABLE",
                    "设备 " + hardwareSn
                            + " 未明确确认 MCU 远程升级线路可用");
        }
        if (!Integer.valueOf(2).equals(asset.fixedFrameRevision())) {
            throw conflict(
                    "DEVICE.MCU_FIRMWARE_PROTOCOL_REVISION_UNSUPPORTED",
                    "设备 " + hardwareSn
                            + " 最近一次确认的 MCU 固定帧协议修订号不是 2");
        }
        if (requireOnline && !"ONLINE".equals(asset.transportStatus())) {
            throw conflict(
                    "DEVICE.MCU_FIRMWARE_DEVICE_OFFLINE",
                    "设备 " + hardwareSn + " 当前未通过 OneNet 在线，整批不会下发");
        }
        return asset;
    }

    private List<Deployment> deployments(long rolloutId, boolean lock) {
        return jdbc.query(DEPLOYMENT_SELECT
                        + " WHERE deployment.rollout_id = ?"
                        + " ORDER BY deployment.wave_no, deployment.id"
                        + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> deployment(rs),
                rolloutId);
    }

    private Deployment requireDeployment(UUID uid, boolean lock) {
        List<Deployment> rows = jdbc.query(
                DEPLOYMENT_SELECT
                        + " WHERE deployment.deployment_uid = ?"
                        + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> deployment(rs),
                uid.toString());
        if (rows.size() != 1) {
            throw new IllegalArgumentException(
                    "firmware progress deployment does not exist");
        }
        return rows.getFirst();
    }

    private WaveState waveState(long rolloutId, int waveNo) {
        return jdbc.queryForObject("""
                        SELECT COUNT(*) AS total,
                               SUM(CASE WHEN deployment_status = 'SUCCEEDED'
                                   THEN 1 ELSE 0 END) AS succeeded,
                               SUM(CASE WHEN deployment_status IN (
                                   'ROLLED_BACK', 'FAILED_LOCKED', 'REJECTED', 'LOCAL_CANCELLED'
                               ) THEN 1 ELSE 0 END) AS failed
                        FROM dev_mcu_firmware_deployment
                        WHERE rollout_id = ? AND wave_no = ?
                        """,
                (rs, ignored) -> new WaveState(
                        rs.getLong("total"),
                        rs.getLong("succeeded"),
                        rs.getLong("failed")),
                rolloutId,
                waveNo);
    }

    private RolloutView rolloutView(
            RolloutRow rollout,
            boolean includeDeployments) {
        ReleaseRow release = releaseById(rollout.releaseId(), false);
        List<Deployment> rows = deployments(rollout.id(), false);
        long pending = rows.stream()
                .filter(item -> "PENDING".equals(item.status())).count();
        long running = rows.stream()
                .filter(item -> RUNNING.contains(item.status())).count();
        long succeeded = rows.stream()
                .filter(item -> "SUCCEEDED".equals(item.status())).count();
        long rolledBack = rows.stream()
                .filter(item -> "ROLLED_BACK".equals(item.status())).count();
        long failed = rows.stream()
                .filter(item -> Set.of("FAILED_LOCKED", "REJECTED", "LOCAL_CANCELLED")
                        .contains(item.status())).count();
        String validationHardwareSn = rows.stream()
                .filter(item -> "VALIDATION".equals(item.kind()))
                .map(Deployment::hardwareSn)
                .findFirst()
                .orElseThrow();
        return new RolloutView(
                rollout.uid(),
                releaseView(release),
                rollout.status(),
                rollout.batchSize(),
                rollout.maximumWaveNo(),
                rollout.currentWaveNo(),
                validationHardwareSn,
                rollout.reason(),
                rollout.createdBy(),
                rollout.promotedBy(),
                instant(rollout.promotedAt()),
                rollout.stoppedBy(),
                instant(rollout.stoppedAt()),
                rollout.stopReason(),
                pending,
                running,
                succeeded,
                rolledBack,
                failed,
                instant(rollout.createdAt()),
                instant(rollout.updatedAt()),
                includeDeployments
                        ? rows.stream().map(this::deploymentView).toList()
                        : List.of());
    }

    private DeploymentView deploymentView(Deployment row) {
        return new DeploymentView(
                row.uid(),
                row.hardwareSn(),
                row.tenantCode(),
                row.organizationCode(),
                row.kind(),
                row.waveNo(),
                row.status(),
                row.commandUid(),
                row.taskUid(),
                row.edgeUpdateUid(),
                row.targetAttempts(),
                row.rollbackAttempts(),
                row.installedVersion(),
                row.installedVersionCode(),
                row.installedIdentityHex(),
                row.errorCode(),
                instant(row.queuedAt()),
                instant(row.completedAt()),
                instant(row.updatedAt()));
    }

    private ReleaseView releaseView(ReleaseRow row) {
        return new ReleaseView(
                row.uid(),
                row.version(),
                row.versionCode(),
                row.identityHex(),
                row.hardwareCompatibility(),
                row.fixedFrameRevision(),
                row.objectKey(),
                HexFormat.of().formatHex(row.packageSha256()),
                row.packageSize(),
                row.status(),
                row.releaseNotes(),
                row.createdBy(),
                row.promotedBy(),
                instant(row.promotedAt()),
                instant(row.createdAt()));
    }

    private ReleaseRow requireRelease(UUID uid, boolean lock) {
        List<ReleaseRow> rows = jdbc.query(
                RELEASE_SELECT + " WHERE firmware_release.release_uid = ?"
                        + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> releaseRow(rs),
                uid.toString());
        if (rows.size() != 1) {
            throw notFound("固件发布不存在");
        }
        return rows.getFirst();
    }

    private ReleaseRow releaseByOperation(UUID operationUid, boolean lock) {
        List<ReleaseRow> rows = jdbc.query(
                RELEASE_SELECT + " WHERE firmware_release.operation_uid = ?"
                        + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> releaseRow(rs),
                operationUid.toString());
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private ReleaseRow releaseById(long id, boolean lock) {
        List<ReleaseRow> rows = jdbc.query(
                RELEASE_SELECT + " WHERE firmware_release.id = ?"
                        + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> releaseRow(rs),
                id);
        if (rows.size() != 1) {
            throw new IllegalStateException("firmware release disappeared");
        }
        return rows.getFirst();
    }

    private RolloutRow requireRollout(UUID uid, boolean lock) {
        List<RolloutRow> rows = jdbc.query(
                ROLLOUT_SELECT + " WHERE rollout.rollout_uid = ?"
                        + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> rolloutRow(rs),
                uid.toString());
        if (rows.size() != 1) {
            throw notFound("固件灰度计划不存在");
        }
        return rows.getFirst();
    }

    private RolloutRow rolloutByOperation(UUID uid, boolean lock) {
        List<RolloutRow> rows = jdbc.query(
                ROLLOUT_SELECT + " WHERE rollout.operation_uid = ?"
                        + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> rolloutRow(rs),
                uid.toString());
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private ActionReplay actionReplay(UUID operationUid) {
        List<ActionReplay> rows = jdbc.query("""
                        SELECT action.action_type, action.reason,
                               rollout.rollout_uid
                        FROM dev_mcu_firmware_rollout_action action
                        JOIN dev_mcu_firmware_rollout rollout
                          ON rollout.id = action.rollout_id
                        WHERE action.operation_uid = ?
                        """,
                (rs, ignored) -> new ActionReplay(
                        UUID.fromString(rs.getString("rollout_uid")),
                        rs.getString("action_type"),
                        rs.getString("reason")),
                operationUid.toString());
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private boolean progressExists(UUID eventUid, long sourceInboxId) {
        return jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_mcu_firmware_progress
                        WHERE event_uid = ? OR source_inbox_id = ?
                        """,
                Long.class,
                eventUid.toString(),
                sourceInboxId) > 0;
    }

    private AuthorizedDeviceScope authorizePlatform() {
        AuthorizedDeviceScope actor = authorization.authorize(
                new DeviceScopeAuthorizationQuery(
                        true, null, null, "device.manage"));
        if (!actor.platformActor()) {
            throw new TargetApiException(
                    403,
                    "IDENTITY.PLATFORM_REQUIRED",
                    "只有平台管理员可以管理 MCU 固件灰度");
        }
        return actor;
    }

    private static long platformAdminId(AuthorizedDeviceScope actor) {
        Long[] result = new Long[1];
        actor.persistenceRef().writeForeignKeysTo(
                (tenantId, organizationId, platformAdminId, staffId) -> {
                    if (platformAdminId == null
                            || tenantId != null
                            || organizationId != null
                            || staffId != null) {
                        throw new TargetApiException(
                                403,
                                "IDENTITY.PLATFORM_REQUIRED",
                                "只有平台管理员可以管理 MCU 固件灰度");
                    }
                    result[0] = platformAdminId;
                });
        return result[0];
    }

    private void requireReleaseReplay(
            ReleaseRow replay,
            RegisterReleaseRequest request) {
        boolean equal = replay.uid().equals(request.releaseUid())
                && replay.version().equals(request.firmwareVersion())
                && replay.versionCode() == request.firmwareVersionCode()
                && replay.identityHex().equals(request.firmwareIdentityHex())
                && replay.hardwareCompatibility().equals(
                request.hardwareCompatibility())
                && replay.fixedFrameRevision()
                == request.fixedFrameRevision()
                && replay.objectKey().equals(request.objectKey())
                && Arrays.equals(
                replay.packageSha256(),
                HexFormat.of().parseHex(request.packageSha256()))
                && replay.packageSize() == request.packageSize();
        equal = equal && Objects.equals(
                replay.releaseNotes(),
                nullableTrimmed(request.releaseNotes()));
        if (!equal) {
            throw conflict(
                    "DEVICE.IDEMPOTENCY_CONFLICT",
                    "同一幂等键不能登记另一份固件发布");
        }
    }

    private void requireRolloutReplay(
            RolloutRow replay,
            CreateRolloutRequest request) {
        ReleaseRow release = releaseById(replay.releaseId(), false);
        List<Deployment> rows = deployments(replay.id(), false);
        String validationHardwareSn = hardwareSn(
                request.validationHardwareSn());
        LinkedHashSet<String> expectedTargets = new LinkedHashSet<>();
        for (String candidate : request.targetHardwareSns()) {
            String normalized = hardwareSn(candidate);
            if (!normalized.equals(validationHardwareSn)) {
                expectedTargets.add(normalized);
            }
        }
        Set<String> actualTargets = rows.stream()
                .filter(item -> "WAVE".equals(item.kind()))
                .map(Deployment::hardwareSn)
                .collect(java.util.stream.Collectors.toSet());
        boolean equal = release.uid().equals(request.releaseUid())
                && replay.batchSize() == request.batchSize()
                && replay.reason().equals(request.reason().trim())
                && rows.stream().anyMatch(item ->
                "VALIDATION".equals(item.kind())
                        && validationHardwareSn.equals(item.hardwareSn()))
                && actualTargets.equals(expectedTargets);
        if (!equal) {
            throw conflict(
                    "DEVICE.IDEMPOTENCY_CONFLICT",
                    "同一幂等键不能创建另一项固件灰度计划");
        }
    }

    private static boolean progressAdvances(
            Deployment deployment,
            String stage,
            int targetAttempts,
            int rollbackAttempts) {
        if (targetAttempts < deployment.targetAttempts()
                || rollbackAttempts < deployment.rollbackAttempts()) {
            return false;
        }
        if (TARGET_ATTEMPT_STAGES.contains(stage)
                && (targetAttempts == 0 || rollbackAttempts != 0)) {
            throw new IllegalArgumentException(
                    "target progress has impossible attempt counters");
        }
        if (ROLLBACK_ATTEMPT_STAGES.contains(stage)
                && rollbackAttempts == 0) {
            throw new IllegalArgumentException(
                    "rollback progress has no rollback attempt");
        }
        if ("REJECTED".equals(stage)
                && (targetAttempts != 0 || rollbackAttempts != 0)) {
            throw new IllegalArgumentException(
                    "rejected progress cannot follow a flash attempt");
        }
        if ("PACKAGE_FETCH_FAILED".equals(stage)
                && (targetAttempts != 0 || rollbackAttempts != 0)) {
            throw new IllegalArgumentException(
                    "package acquisition failed after a flash attempt");
        }
        if (rollbackAttempts > 0
                && !ROLLBACK_ATTEMPT_STAGES.contains(stage)
                && !"FAILED_LOCKED".equals(stage)) {
            throw new IllegalArgumentException(
                    "firmware progress returned to the target after rollback");
        }
        if (targetAttempts == deployment.targetAttempts()
                && rollbackAttempts == deployment.rollbackAttempts()
                && STAGE_ORDER.get(stage)
                < STAGE_ORDER.get(deployment.status())) {
            return false;
        }
        return true;
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static String hardwareSn(String value) {
        if (value == null
                || !value.matches("^[A-Za-z0-9_-]{8,64}$")) {
            throw invalid("设备硬件 SN 格式不正确");
        }
        return value;
    }

    private static int page(int value) {
        return value < 1 ? 1 : value;
    }

    private static int pageSize(int value) {
        return Math.max(1, Math.min(100, value));
    }

    private static String nullableTrimmed(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    private static void requireUuidV4(UUID value, String field) {
        if (value == null
                || value.version() != 4
                || value.variant() != 2) {
            throw invalid(field + " 必须是 UUIDv4");
        }
    }

    private static String timestamp(LocalDateTime value) {
        return DateTimeFormatter.ISO_INSTANT.format(
                value.toInstant(ZoneOffset.UTC));
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static JsonNode requiredObject(JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(field + " must be an object");
        }
        return value;
    }

    private static String text(JsonNode parent, String field, int maximum) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null
                || !value.isTextual()
                || value.asText().isBlank()
                || value.asText().length() > maximum) {
            throw new IllegalArgumentException(field + " is invalid");
        }
        return value.asText();
    }

    private static void requireText(
            JsonNode parent, String field, String expected) {
        if (!expected.equals(text(parent, field, 64))) {
            throw new IllegalArgumentException(field + " differs");
        }
    }

    private static String pattern(
            JsonNode parent,
            String field,
            String pattern,
            int maximum) {
        String value = text(parent, field, maximum);
        if (!value.matches(pattern)) {
            throw new IllegalArgumentException(field + " has invalid format");
        }
        return value;
    }

    private static String nullablePattern(
            JsonNode parent,
            String field,
            String pattern,
            int maximum) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        return pattern(parent, field, pattern, maximum);
    }

    private static UUID uuid(JsonNode parent, String field) {
        return UUID.fromString(pattern(parent, field, UUID_V4, 36));
    }

    private static UUID nullableUuid(JsonNode parent, String field) {
        String value = nullablePattern(parent, field, UUID_V4, 36);
        return value == null ? null : UUID.fromString(value);
    }

    private static long integer(
            JsonNode parent, String field, long minimum, long maximum) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null
                || !value.isIntegralNumber()
                || !value.canConvertToLong()
                || value.asLong() < minimum
                || value.asLong() > maximum) {
            throw new IllegalArgumentException(field + " is outside range");
        }
        return value.asLong();
    }

    private static boolean bool(JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isBoolean()) {
            throw new IllegalArgumentException(field + " must be boolean");
        }
        return value.asBoolean();
    }

    private static LocalDateTime nullableInstant(
            JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        try {
            return Instant.parse(text(parent, field, 30))
                    .atOffset(ZoneOffset.UTC)
                    .toLocalDateTime();
        } catch (RuntimeException exception) {
            throw new IllegalArgumentException(field + " is not an instant");
        }
    }

    private static String stage(JsonNode payload) {
        String stage = text(payload, "stage", 32);
        if (!Set.of(
                "QUEUED", "PACKAGE_FETCH_FAILED", "PREFLIGHT",
                "PREPARED", "FLASHING_TARGET",
                "VERIFYING_TARGET", "ROLLING_BACK", "VERIFYING_ROLLBACK",
                "SUCCEEDED", "ROLLED_BACK", "FAILED_LOCKED", "REJECTED")
                .contains(stage)) {
            throw new IllegalArgumentException("unsupported firmware stage");
        }
        return stage;
    }

    private static Installed installed(JsonNode payload) {
        JsonNode version = payload.get("installedFirmwareVersion");
        JsonNode code = payload.get("installedFirmwareVersionCode");
        JsonNode identity = payload.get("installedFirmwareIdentityHex");
        boolean absent = (version == null || version.isNull())
                && (code == null || code.isNull())
                && (identity == null || identity.isNull());
        if (absent) {
            return null;
        }
        if (version == null || version.isNull()
                || code == null || code.isNull()
                || identity == null || identity.isNull()) {
            throw new IllegalArgumentException(
                    "installed firmware identity must be all present or all null");
        }
        return new Installed(
                text(payload, "installedFirmwareVersion", 32),
                integer(payload, "installedFirmwareVersionCode", 1,
                        4_294_967_295L),
                pattern(payload, "installedFirmwareIdentityHex",
                        IDENTITY, 16));
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(
                400, "DEVICE.MCU_FIRMWARE_REQUEST_INVALID", message);
    }

    private static TargetApiException notFound(String message) {
        return new TargetApiException(
                404, "DEVICE.MCU_FIRMWARE_NOT_FOUND", message);
    }

    private static TargetApiException conflict(String code, String message) {
        return new TargetApiException(409, code, message);
    }

    private static ReleaseRow releaseRow(java.sql.ResultSet rs)
            throws java.sql.SQLException {
        return new ReleaseRow(
                rs.getLong("release_id"),
                UUID.fromString(rs.getString("release_uid")),
                rs.getString("firmware_version"),
                rs.getLong("firmware_version_code"),
                rs.getString("firmware_identity_hex"),
                rs.getString("hardware_compatibility"),
                rs.getInt("fixed_frame_revision"),
                rs.getString("package_object_key"),
                rs.getBytes("package_sha256"),
                rs.getLong("package_size"),
                rs.getString("release_status"),
                rs.getString("release_notes"),
                rs.getString("release_created_by"),
                rs.getString("release_promoted_by"),
                localDateTime(rs, "release_promoted_at"),
                localDateTime(rs, "release_created_at"));
    }

    private static RolloutRow rolloutRow(java.sql.ResultSet rs)
            throws java.sql.SQLException {
        return new RolloutRow(
                rs.getLong("rollout_id"),
                UUID.fromString(rs.getString("rollout_uid")),
                rs.getLong("release_id"),
                rs.getString("rollout_status"),
                rs.getInt("batch_size"),
                rs.getInt("maximum_wave_no"),
                rs.getInt("current_wave_no"),
                rs.getString("change_reason"),
                rs.getString("rollout_created_by"),
                rs.getString("rollout_promoted_by"),
                localDateTime(rs, "rollout_promoted_at"),
                rs.getString("rollout_stopped_by"),
                localDateTime(rs, "rollout_stopped_at"),
                rs.getString("stop_reason"),
                localDateTime(rs, "rollout_created_at"),
                localDateTime(rs, "rollout_updated_at"));
    }

    private static Deployment deployment(java.sql.ResultSet rs)
            throws java.sql.SQLException {
        return new Deployment(
                rs.getLong("deployment_id"),
                UUID.fromString(rs.getString("deployment_uid")),
                rs.getLong("rollout_id"),
                rs.getLong("release_id"),
                rs.getLong("asset_id"),
                rs.getString("hardware_sn"),
                rs.getString("tenant_code"),
                rs.getString("organization_code"),
                rs.getString("deployment_kind"),
                rs.getInt("wave_no"),
                rs.getString("deployment_status"),
                nullableUuid(rs.getString("command_uid")),
                nullableUuid(rs.getString("reliable_task_uid")),
                nullableUuid(rs.getString("edge_update_uid")),
                rs.getInt("target_attempt_count"),
                rs.getInt("rollback_attempt_count"),
                rs.getString("installed_firmware_version"),
                rs.getObject("installed_firmware_version_code", Long.class),
                rs.getString("installed_firmware_identity_hex"),
                rs.getString("error_code"),
                localDateTime(rs, "queued_at"),
                localDateTime(rs, "completed_at"),
                localDateTime(rs, "deployment_updated_at"),
                rs.getString("rollout_status"));
    }

    private static UUID nullableUuid(String value) {
        return value == null ? null : UUID.fromString(value);
    }

    private static LocalDateTime localDateTime(
            java.sql.ResultSet rs, String column)
            throws java.sql.SQLException {
        java.sql.Timestamp value = rs.getTimestamp(column);
        return value == null ? null : value.toLocalDateTime();
    }

    private static final String RELEASE_SELECT = """
            SELECT firmware_release.id AS release_id,
                   firmware_release.release_uid,
                   firmware_release.firmware_version,
                   firmware_release.firmware_version_code,
                   firmware_release.firmware_identity_hex,
                   firmware_release.hardware_compatibility,
                   firmware_release.fixed_frame_revision,
                   firmware_release.package_object_key,
                   firmware_release.package_sha256,
                   firmware_release.package_size,
                   firmware_release.release_status,
                   firmware_release.release_notes,
                   creator.display_name AS release_created_by,
                   promoter.display_name AS release_promoted_by,
                   firmware_release.promoted_at AS release_promoted_at,
                   firmware_release.created_at AS release_created_at
            FROM dev_mcu_firmware_release firmware_release
            JOIN iam_platform_admin creator
              ON creator.id = firmware_release.created_by_platform_admin_id
            LEFT JOIN iam_platform_admin promoter
              ON promoter.id = firmware_release.promoted_by_platform_admin_id
            """;

    private static final String ROLLOUT_SELECT = """
            SELECT rollout.id AS rollout_id, rollout.rollout_uid,
                   rollout.release_id, rollout.rollout_status,
                   rollout.batch_size, rollout.maximum_wave_no,
                   rollout.current_wave_no, rollout.change_reason,
                   creator.display_name AS rollout_created_by,
                   promoter.display_name AS rollout_promoted_by,
                   rollout.promoted_at AS rollout_promoted_at,
                   stopper.display_name AS rollout_stopped_by,
                   rollout.stopped_at AS rollout_stopped_at,
                   rollout.stop_reason,
                   rollout.created_at AS rollout_created_at,
                   rollout.updated_at AS rollout_updated_at
            FROM dev_mcu_firmware_rollout rollout
            JOIN iam_platform_admin creator
              ON creator.id = rollout.created_by_platform_admin_id
            LEFT JOIN iam_platform_admin promoter
              ON promoter.id = rollout.promoted_by_platform_admin_id
            LEFT JOIN iam_platform_admin stopper
              ON stopper.id = rollout.stopped_by_platform_admin_id
            """;

    private static final String DEPLOYMENT_SELECT = """
            SELECT deployment.id AS deployment_id,
                   deployment.deployment_uid,
                   deployment.rollout_id, deployment.release_id,
                   deployment.asset_id, asset.hardware_sn,
                   tenant.tenant_code, organization.organization_code,
                   deployment.deployment_kind, deployment.wave_no,
                   deployment.deployment_status, deployment.command_uid,
                   deployment.reliable_task_uid,
                   deployment.edge_update_uid,
                   deployment.target_attempt_count,
                   deployment.rollback_attempt_count,
                   deployment.installed_firmware_version,
                   deployment.installed_firmware_version_code,
                   deployment.installed_firmware_identity_hex,
                   deployment.error_code, deployment.queued_at,
                   deployment.completed_at,
                   deployment.updated_at AS deployment_updated_at,
                   rollout.rollout_status
            FROM dev_mcu_firmware_deployment deployment
            JOIN dev_mcu_firmware_rollout rollout
              ON rollout.id = deployment.rollout_id
            JOIN dev_device_asset asset ON asset.id = deployment.asset_id
            LEFT JOIN iam_tenant tenant ON tenant.id = deployment.tenant_id
            LEFT JOIN iam_organization organization
              ON organization.id = deployment.organization_id
             AND organization.tenant_id = deployment.tenant_id
            """;

    @FunctionalInterface
    private interface ActionMutation {
        Integer apply(
                RolloutRow rollout,
                long adminId,
                String reason,
                LocalDateTime now);
    }

    private record ReleaseRow(
            long id,
            UUID uid,
            String version,
            long versionCode,
            String identityHex,
            String hardwareCompatibility,
            int fixedFrameRevision,
            String objectKey,
            byte[] packageSha256,
            long packageSize,
            String status,
            String releaseNotes,
            String createdBy,
            String promotedBy,
            LocalDateTime promotedAt,
            LocalDateTime createdAt) {

        private ReleaseRow {
            packageSha256 = Arrays.copyOf(
                    packageSha256, packageSha256.length);
        }

        @Override
        public byte[] packageSha256() {
            return Arrays.copyOf(packageSha256, packageSha256.length);
        }
    }

    private record RolloutRow(
            long id,
            UUID uid,
            long releaseId,
            String status,
            int batchSize,
            int maximumWaveNo,
            int currentWaveNo,
            String reason,
            String createdBy,
            String promotedBy,
            LocalDateTime promotedAt,
            String stoppedBy,
            LocalDateTime stoppedAt,
            String stopReason,
            LocalDateTime createdAt,
            LocalDateTime updatedAt) {
    }

    private record Deployment(
            long id,
            UUID uid,
            long rolloutId,
            long releaseId,
            long assetId,
            String hardwareSn,
            String tenantCode,
            String organizationCode,
            String kind,
            int waveNo,
            String status,
            UUID commandUid,
            UUID taskUid,
            UUID edgeUpdateUid,
            int targetAttempts,
            int rollbackAttempts,
            String installedVersion,
            Long installedVersionCode,
            String installedIdentityHex,
            String errorCode,
            LocalDateTime queuedAt,
            LocalDateTime completedAt,
            LocalDateTime updatedAt,
            String rolloutStatus) {
    }

    private record Asset(
            long id,
            String hardwareSn,
            Long tenantId,
            Long organizationId,
            String lifecycleStatus,
            String acceptanceStatus,
            Integer fixedFrameRevision,
            Boolean remoteUpdateCapable,
            String transportStatus) {
    }

    private record Installed(
            String version,
            long versionCode,
            String identityHex) {
    }

    private record WaveState(long total, long succeeded, long failed) {
    }

    private record ActionReplay(
            UUID rolloutUid,
            String action,
            String reason) {
    }
}
