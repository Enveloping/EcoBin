package org.enveloping.ecobin.device.application.software;

import org.enveloping.ecobin.device.api.port.BusinessReleaseArtifactStoragePort;
import org.enveloping.ecobin.device.api.port.BusinessReleaseSigningKeyPort;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedPlatformDeviceAssetFactEvent;
import org.enveloping.ecobin.device.application.software.BusinessReleasePackageVerifier.VerificationException;
import org.enveloping.ecobin.device.application.software.BusinessReleasePackageVerifier.VerifiedRelease;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.CompatibilityDeclarationView;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.ControlPlaneReadinessView;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.CreateReleaseDraftRequest;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.CreateRolloutRequest;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.DeploymentView;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.PageData;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.ReleaseActionView;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.ReleaseView;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.RolloutActionView;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.RolloutView;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWake;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWakePort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.DeviceScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeviceScope;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.Base64;
import java.util.Locale;

/** Business-runtime release, validation dispatch, and progress control plane. */
@Service
public class BusinessReleaseControlPlaneService {

    private static final String BUSINESS_RELEASE_BASELINE = "BUSINESS_RELEASE";
    private static final String IMAGE_BRIDGE_BASELINE = "IMAGE_BRIDGE";

    public static final String EVENT_TYPE =
            "BUSINESS_RUNTIME_UPDATE_PROGRESS";
    public static final String CANCEL_EVENT_TYPE =
            "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT";
    private static final String COMMAND_TYPE =
            "START_BUSINESS_RUNTIME_UPDATE";
    private static final String CANCEL_COMMAND_TYPE =
            "CANCEL_BUSINESS_RUNTIME_UPDATE";
    private static final String TARGET_TYPE =
            "BUSINESS_RUNTIME_DEPLOYMENT";

    private static final int DEFAULT_BATCH_SIZE = 3;
    private static final int POLICY_SECONDS = 30 * 60;
    private static final int VERIFICATION_LEASE_SECONDS = 30 * 60;
    private static final int MAXIMUM_RETRIES = 3;
    private static final int BACKEND_COMMAND_CONTRACT_VERSION = 2;
    private static final int DEVICE_EVENT_CONTRACT_VERSION = 2;
    private static final String SEMANTIC_VERSION =
            "^(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)\\."
                    + "(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+"
                    + "(?:\\.[0-9A-Za-z-]+)*)?(?:\\+[0-9A-Za-z-]+"
                    + "(?:\\.[0-9A-Za-z-]+)*)?$";
    private static final String SHA256 = "^[0-9a-f]{64}$";
    private static final String UUID_V4 =
            "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$";
    private static final Set<String> COMPATIBLE = Set.of(
            "FULLY_COMPATIBLE", "BASE_COMPATIBLE");
    private static final Set<String> DEPLOYMENT_TERMINAL = Set.of(
            "SUCCEEDED", "ROLLED_BACK", "DEFERRED", "REJECTED",
            "FAILED_LOCKED", "CANCELLED", "LOCAL_CANCELLED");
    private static final Set<String> ERROR_STAGES = Set.of(
            "DEFERRED", "REJECTED", "FAILED_LOCKED",
            "DOWNLOAD_AUTHORIZATION_REQUIRED");

    private final JdbcTemplate jdbc;
    private final DeviceScopeAuthorizationPort authorization;
    private final BusinessReleaseArtifactStoragePort artifacts;
    private final BusinessReleaseSigningKeyPort signingKeys;
    private final BusinessReleasePackageVerifier verifier;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final TransactionTemplate transactions;
    private final boolean remoteDispatchEnabled;
    private final ObjectMapper objectMapper;
    private final PlatformDeviceAssetTaskRefFactory taskRefFactory;
    private final ReliablePlatformDeviceControlTaskRegistrationPort tasks;
    private final ReliableDeviceTaskProofPort taskProof;
    private final ReliableTaskWakePort taskWake;
    private final DeviceSoftwareCompatibilityService compatibility;

    public BusinessReleaseControlPlaneService(
            JdbcTemplate jdbc,
            DeviceScopeAuthorizationPort authorization,
            BusinessReleaseArtifactStoragePort artifacts,
            BusinessReleaseSigningKeyPort signingKeys,
            BusinessReleasePackageVerifier verifier,
            DeviceConfigurationCanonicalizer canonicalizer,
            PlatformTransactionManager transactionManager,
            @Value("${ecobin.device.business-release.remote-dispatch-enabled:false}")
            boolean remoteDispatchEnabled) {
        this(
                jdbc,
                authorization,
                artifacts,
                signingKeys,
                verifier,
                canonicalizer,
                transactionManager,
                null,
                null,
                null,
                null,
                null,
                null,
                remoteDispatchEnabled);
    }

    public BusinessReleaseControlPlaneService(
            JdbcTemplate jdbc,
            DeviceScopeAuthorizationPort authorization,
            BusinessReleaseArtifactStoragePort artifacts,
            BusinessReleaseSigningKeyPort signingKeys,
            BusinessReleasePackageVerifier verifier,
            DeviceConfigurationCanonicalizer canonicalizer,
            PlatformTransactionManager transactionManager,
            ObjectMapper objectMapper,
            PlatformDeviceAssetTaskRefFactory taskRefFactory,
            ReliablePlatformDeviceControlTaskRegistrationPort tasks,
            ReliableDeviceTaskProofPort taskProof,
            ReliableTaskWakePort taskWake,
            boolean remoteDispatchEnabled) {
        this(
                jdbc,
                authorization,
                artifacts,
                signingKeys,
                verifier,
                canonicalizer,
                transactionManager,
                objectMapper,
                taskRefFactory,
                tasks,
                taskProof,
                taskWake,
                new DeviceSoftwareCompatibilityService(jdbc, objectMapper),
                remoteDispatchEnabled);
    }

    @Autowired
    public BusinessReleaseControlPlaneService(
            JdbcTemplate jdbc,
            DeviceScopeAuthorizationPort authorization,
            BusinessReleaseArtifactStoragePort artifacts,
            BusinessReleaseSigningKeyPort signingKeys,
            BusinessReleasePackageVerifier verifier,
            DeviceConfigurationCanonicalizer canonicalizer,
            PlatformTransactionManager transactionManager,
            ObjectMapper objectMapper,
            PlatformDeviceAssetTaskRefFactory taskRefFactory,
            ReliablePlatformDeviceControlTaskRegistrationPort tasks,
            ReliableDeviceTaskProofPort taskProof,
            ReliableTaskWakePort taskWake,
            DeviceSoftwareCompatibilityService compatibility,
            @Value("${ecobin.device.business-release.remote-dispatch-enabled:false}")
            boolean remoteDispatchEnabled) {
        this.jdbc = jdbc;
        this.authorization = authorization;
        this.artifacts = artifacts;
        this.signingKeys = signingKeys;
        this.verifier = verifier;
        this.canonicalizer = canonicalizer;
        this.transactions = new TransactionTemplate(transactionManager);
        this.remoteDispatchEnabled = remoteDispatchEnabled;
        this.objectMapper = objectMapper;
        this.taskRefFactory = taskRefFactory;
        this.tasks = tasks;
        this.taskProof = taskProof;
        this.taskWake = taskWake;
        this.compatibility = compatibility;
    }

    @Transactional(readOnly = true)
    public ControlPlaneReadinessView readiness() {
        authorizePlatform();
        BusinessReleaseArtifactStoragePort.Readiness artifact =
                artifacts.readiness();
        BusinessReleaseSigningKeyPort.Readiness keys = signingKeys.readiness();
        return new ControlPlaneReadinessView(
                artifact.available(),
                artifact.message(),
                keys.available(),
                keys.message(),
                remoteDispatchEnabled,
                remoteDispatchEnabled
                        ? "真实远程下发已显式开启；新计划会冻结这一开关状态"
                        : "真实远程下发默认关闭；当前只能校验发布并演练灰度计划");
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public ReleaseView createDraft(
            UUID operationUid,
            CreateReleaseDraftRequest request) {
        requireUuidV4(operationUid, "Idempotency-Key");
        if (request == null) {
            throw invalid("发布草稿不能为空");
        }
        String versionName = versionName(request.versionName());
        String notes = nullableTrimmed(request.releaseNotes(), 1000, "发布说明");
        AuthorizedDeviceScope actor = authorizePlatform();
        TargetWebAuditRequestContext.describe(
                "device.business-release.create", versionName);
        ReleaseRow replay = releaseByCreateOperation(operationUid, false);
        if (replay != null) {
            if (!replay.versionName().equals(versionName)
                    || !Objects.equals(replay.releaseNotes(), notes)) {
                throw idempotencyConflict("同一幂等键不能创建另一份业务发布草稿");
            }
            return releaseView(replay, true);
        }
        long adminId = platformAdminId(actor);
        LocalDateTime now = databaseNow();
        jdbc.queryForObject("""
                SELECT singleton_id
                FROM dev_edge_software_release_sequence
                WHERE singleton_id = 1
                FOR UPDATE
                """, Long.class);
        long sequence = jdbc.queryForObject("""
                SELECT last_release_sequence + 1
                FROM dev_edge_software_release_sequence
                WHERE singleton_id = 1
                """, Long.class);
        if (sequence > 9_007_199_254_740_991L) {
            throw conflict(
                    "DEVICE.BUSINESS_RELEASE_SEQUENCE_EXHAUSTED",
                    "业务发布顺序号已经用尽");
        }
        UUID releaseUid = UUID.randomUUID();
        int updated = jdbc.update("""
                UPDATE dev_edge_software_release_sequence
                SET last_release_sequence = ?,
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE singleton_id = 1
                """, sequence, now);
        if (updated != 1) {
            throw new IllegalStateException("business release sequence row is missing");
        }
        KeyHolder key = new GeneratedKeyHolder();
        try {
            jdbc.update(connection -> {
                var statement = connection.prepareStatement("""
                        INSERT INTO dev_edge_software_release_control (
                            release_uid, create_operation_uid,
                            version_name, release_sequence, release_status,
                            package_object_key, signature_object_key,
                            release_notes, created_by_platform_admin_id,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?)
                        """, java.sql.Statement.RETURN_GENERATED_KEYS);
                statement.setString(1, releaseUid.toString());
                statement.setString(2, operationUid.toString());
                statement.setString(3, versionName);
                statement.setLong(4, sequence);
                statement.setString(5, packageKey(releaseUid));
                statement.setString(6, signatureKey(releaseUid));
                statement.setString(7, notes);
                statement.setLong(8, adminId);
                statement.setObject(9, now);
                statement.setObject(10, now);
                return statement;
            }, key);
        } catch (DuplicateKeyException exception) {
            throw conflict(
                    "DEVICE.BUSINESS_RELEASE_CONFLICT",
                    "该业务版本或发布身份已经存在");
        }
        long releaseId = requireGeneratedId(key, "business release");
        insertReleaseAction(
                operationUid,
                releaseId,
                "CREATE",
                adminId,
                "创建业务发布草稿",
                "DRAFT",
                now);
        return releaseView(requireRelease(releaseUid, false), true);
    }

    @Transactional(readOnly = true)
    public PageData<ReleaseView> listReleases(int page, int pageSize) {
        authorizePlatform();
        int safePage = page(page);
        int safeSize = pageSize(pageSize);
        long total = jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_edge_software_release_control",
                Long.class);
        List<ReleaseView> items = jdbc.query(
                RELEASE_SELECT
                        + " ORDER BY release_control.created_at DESC,"
                        + " release_control.id DESC LIMIT ? OFFSET ?",
                (rs, ignored) -> releaseView(releaseRow(rs), false),
                safeSize,
                (safePage - 1) * safeSize);
        return new PageData<>(items, safePage, safeSize, total);
    }

    @Transactional(readOnly = true)
    public ReleaseView releaseDetail(UUID releaseUid) {
        authorizePlatform();
        requireUuidV4(releaseUid, "releaseUid");
        return releaseView(requireRelease(releaseUid, false), true);
    }

    public ReleaseView uploadArtifacts(
            UUID operationUid,
            UUID releaseUid,
            Path packagePath,
            Path signaturePath,
            String signingKeyId,
            String reason) {
        requireUuidV4(operationUid, "Idempotency-Key");
        requireUuidV4(releaseUid, "releaseUid");
        String normalizedReason = requiredReason(reason);
        String normalizedKeyId = signingKeyId(signingKeyId);
        long adminId = authorizePlatformAdminForExternalIo();
        TargetWebAuditRequestContext.describe(
                "device.business-release.artifact.upload", releaseUid.toString());
        ReleaseRow release = requireRelease(releaseUid, false);
        FileIdentity packageIdentity = fileIdentity(
                packagePath,
                BusinessReleasePackageVerifier.MAXIMUM_PACKAGE_BYTES,
                "发布包");
        FileIdentity signatureIdentity = fileIdentity(signaturePath, 64, "签名文件");
        if (signatureIdentity.size() != 64) {
            throw invalid("签名文件必须恰好为 64 字节");
        }
        ReleaseActionRow replay = releaseAction(operationUid);
        if (replay != null) {
            requireActionReplay(
                    replay, release.id(), "UPLOAD", normalizedReason);
            requireArtifactReplay(
                    release,
                    normalizedKeyId,
                    packageIdentity,
                    signatureIdentity);
            return releaseView(requireRelease(releaseUid, false), true);
        }
        if (!"DRAFT".equals(release.status()) || release.artifactUploaded()) {
            throw conflict(
                    "DEVICE.BUSINESS_RELEASE_ARTIFACT_IMMUTABLE",
                    "发布包已经上传或发布已离开草稿状态，不能覆盖制品");
        }
        requireArtifactStorage();
        byte[] signatureBytes;
        try {
            signatureBytes = Files.readAllBytes(signaturePath);
        } catch (IOException exception) {
            throw invalid("签名文件不可读");
        }
        artifacts.storeImmutable(
                release.packageObjectKey(),
                packagePath,
                packageIdentity.sha256(),
                packageIdentity.size());
        artifacts.storeImmutable(
                release.signatureObjectKey(),
                signaturePath,
                signatureIdentity.sha256(),
                signatureIdentity.size());

        return transactions.execute(status -> {
            ReleaseRow locked = requireRelease(releaseUid, true);
            ReleaseActionRow concurrentReplay = releaseAction(operationUid);
            if (concurrentReplay != null) {
                requireActionReplay(
                        concurrentReplay,
                        locked.id(),
                        "UPLOAD",
                        normalizedReason);
                requireArtifactReplay(
                        locked,
                        normalizedKeyId,
                        packageIdentity,
                        signatureIdentity);
                return releaseView(requireRelease(releaseUid, false), true);
            }
            if (!"DRAFT".equals(locked.status()) || locked.artifactUploaded()) {
                throw conflict(
                        "DEVICE.BUSINESS_RELEASE_ARTIFACT_IMMUTABLE",
                        "发布包已经由另一项操作上传，不能覆盖");
            }
            LocalDateTime now = databaseNow();
            int updated = jdbc.update("""
                    UPDATE dev_edge_software_release_control
                    SET package_sha256 = ?, package_size = ?,
                        signature_sha256 = ?, signature_bytes = ?,
                        signing_key_id = ?, artifact_uploaded_at = ?,
                        updated_at = ?, lock_version = lock_version + 1
                    WHERE id = ? AND release_status = 'DRAFT'
                      AND package_sha256 IS NULL
                    """,
                    HexFormat.of().parseHex(packageIdentity.sha256()),
                    packageIdentity.size(),
                    HexFormat.of().parseHex(signatureIdentity.sha256()),
                    signatureBytes,
                    normalizedKeyId,
                    now,
                    now,
                    locked.id());
            if (updated != 1) {
                throw conflict(
                        "DEVICE.BUSINESS_RELEASE_CHANGED",
                        "发布草稿已发生变化，请刷新后重试");
            }
            insertReleaseAction(
                    operationUid,
                    locked.id(),
                    "UPLOAD",
                    adminId,
                    normalizedReason,
                    "DRAFT",
                    now);
            return releaseView(requireRelease(releaseUid, false), true);
        });
    }

    public ReleaseView verify(
            UUID operationUid,
            UUID releaseUid,
            String reason) {
        requireUuidV4(operationUid, "Idempotency-Key");
        requireUuidV4(releaseUid, "releaseUid");
        String normalizedReason = requiredReason(reason);
        long adminId = authorizePlatformAdminForExternalIo();
        TargetWebAuditRequestContext.describe(
                "device.business-release.verify", releaseUid.toString());

        ReleaseRow prepared = transactions.execute(status -> {
            ReleaseRow locked = requireRelease(releaseUid, true);
            ReleaseActionRow replay = releaseAction(operationUid);
            if (replay != null) {
                requireActionReplay(
                        replay,
                        locked.id(),
                        "START_VERIFICATION",
                        normalizedReason);
                if (!"VERIFYING".equals(locked.status())
                        || !operationUid.equals(
                        locked.verificationOperationUid())) {
                    return locked;
                }
                return locked;
            }
            LocalDateTime now = databaseNow();
            boolean staleVerification = "VERIFYING".equals(locked.status())
                    && !locked.updatedAt()
                    .plusSeconds(VERIFICATION_LEASE_SECONDS)
                    .isAfter(now);
            if ((!Set.of("DRAFT", "VERIFICATION_FAILED")
                    .contains(locked.status()) && !staleVerification)
                    || !locked.artifactUploaded()) {
                if ("VERIFYING".equals(locked.status())) {
                    throw conflict(
                            "DEVICE.BUSINESS_RELEASE_VERIFICATION_RUNNING",
                            "发布包正在校验；若后台中断，可在 30 分钟后重新发起校验");
                }
                throw conflict(
                        "DEVICE.BUSINESS_RELEASE_NOT_VERIFIABLE",
                        "只有已经上传制品的草稿或校验失败发布可以重新校验");
            }
            jdbc.update("""
                    UPDATE dev_edge_software_release_control
                    SET release_status = 'VERIFYING',
                        verification_operation_uid = ?,
                        verification_error_code = NULL,
                        verification_error_message = NULL,
                        updated_at = ?, lock_version = lock_version + 1
                    WHERE id = ?
                    """, operationUid.toString(), now, locked.id());
            insertReleaseAction(
                    operationUid,
                    locked.id(),
                    "START_VERIFICATION",
                    adminId,
                    normalizedReason,
                    "VERIFYING",
                    now);
            return requireRelease(releaseUid, false);
        });
        if (!"VERIFYING".equals(prepared.status())
                || !operationUid.equals(
                prepared.verificationOperationUid())) {
            return releaseView(prepared, true);
        }
        requireArtifactStorage();
        Path directory = null;
        try {
            directory = Files.createTempDirectory("ecobin-release-verify-");
            Path packagePath = directory.resolve("package.tar.gz");
            Path signaturePath = directory.resolve("package.sig");
            artifacts.download(prepared.packageObjectKey(), packagePath);
            artifacts.download(prepared.signatureObjectKey(), signaturePath);
            FileIdentity downloadedPackage = fileIdentity(
                    packagePath,
                    BusinessReleasePackageVerifier.MAXIMUM_PACKAGE_BYTES,
                    "发布包");
            FileIdentity downloadedSignature = fileIdentity(
                    signaturePath, 64, "签名文件");
            if (!downloadedPackage.sha256().equals(prepared.packageSha256())
                    || downloadedPackage.size() != prepared.packageSize()
                    || !downloadedSignature.sha256().equals(
                    prepared.signatureSha256())
                    || downloadedSignature.size() != 64) {
                throw new VerificationException(
                        "STORED_ARTIFACT_MISMATCH",
                        "从私有存储读回的发布制品与上传记录不一致");
            }
            VerifiedRelease verified = verifier.verify(
                    packagePath,
                    signaturePath,
                    prepared.signingKeyId(),
                    prepared.uid(),
                    prepared.versionName(),
                    prepared.releaseSequence());
            return completeVerification(
                    operationUid, prepared.uid(), adminId, normalizedReason, verified);
        } catch (VerificationException exception) {
            return failVerification(
                    operationUid,
                    prepared.uid(),
                    adminId,
                    normalizedReason,
                    exception.code(),
                    exception.getMessage());
        } catch (Exception exception) {
            return failVerification(
                    operationUid,
                    prepared.uid(),
                    adminId,
                    normalizedReason,
                    "VERIFICATION_INFRASTRUCTURE_UNAVAILABLE",
                    "发布制品存储或验签配置暂时不可用");
        } finally {
            deleteVerificationDirectory(directory);
        }
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public ReleaseView approve(
            UUID operationUid,
            UUID releaseUid,
            String reason) {
        return transitionRelease(
                operationUid,
                releaseUid,
                reason,
                "AWAITING_APPROVAL",
                "READY",
                "APPROVE",
                "device.business-release.approve");
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public ReleaseView suspend(
            UUID operationUid,
            UUID releaseUid,
            String reason) {
        return transitionRelease(
                operationUid,
                releaseUid,
                reason,
                "READY",
                "SUSPENDED",
                "SUSPEND",
                "device.business-release.suspend");
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public ReleaseView resume(
            UUID operationUid,
            UUID releaseUid,
            String reason) {
        return transitionRelease(
                operationUid,
                releaseUid,
                reason,
                "SUSPENDED",
                "READY",
                "RESUME",
                "device.business-release.resume");
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public ReleaseView retire(
            UUID operationUid,
            UUID releaseUid,
            String reason) {
        requireUuidV4(operationUid, "Idempotency-Key");
        requireUuidV4(releaseUid, "releaseUid");
        String normalizedReason = requiredReason(reason);
        AuthorizedDeviceScope actor = authorizePlatform();
        TargetWebAuditRequestContext.describe(
                "device.business-release.retire", releaseUid.toString());
        ReleaseRow release = requireRelease(releaseUid, true);
        ReleaseActionRow replay = releaseAction(operationUid);
        if (replay != null) {
            requireActionReplay(
                    replay, release.id(), "RETIRE", normalizedReason);
            return releaseView(requireRelease(releaseUid, false), true);
        }
        if (!Set.of("READY", "SUSPENDED").contains(release.status())) {
            throw invalidTransition("只有可用于下发或已暂停的发布可以归档");
        }
        long adminId = platformAdminId(actor);
        LocalDateTime now = databaseNow();
        jdbc.update("""
                UPDATE dev_edge_software_release_control
                SET release_status = 'RETIRED',
                    retired_by_platform_admin_id = ?,
                    retired_at = ?, retirement_reason = ?,
                    updated_at = ?, lock_version = lock_version + 1
                WHERE id = ?
                """, adminId, now, normalizedReason, now, release.id());
        insertReleaseAction(
                operationUid,
                release.id(),
                "RETIRE",
                adminId,
                normalizedReason,
                "RETIRED",
                now);
        return releaseView(requireRelease(releaseUid, false), true);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public RolloutView createRollout(
            UUID operationUid,
            CreateRolloutRequest request) {
        requireUuidV4(operationUid, "Idempotency-Key");
        if (request == null) {
            throw invalid("灰度计划不能为空");
        }
        requireUuidV4(request.releaseUid(), "releaseUid");
        String validationSn = hardwareSn(request.validationHardwareSn());
        int batchSize = request.batchSize() == null
                ? DEFAULT_BATCH_SIZE : request.batchSize();
        if (batchSize < 1 || batchSize > 100) {
            throw invalid("每批设备数量必须在 1 到 100 之间");
        }
        String reason = requiredReason(request.reason());
        LinkedHashSet<String> targets = new LinkedHashSet<>();
        targets.add(validationSn);
        for (String hardwareSn : request.targetHardwareSns()) {
            targets.add(hardwareSn(hardwareSn));
        }
        if (targets.size() > 1001) {
            throw invalid("一次灰度计划最多选择 1001 台设备");
        }
        AuthorizedDeviceScope actor = authorizePlatform();
        TargetWebAuditRequestContext.describe(
                "device.business-release.rollout.create", operationUid.toString());
        RolloutRow replay = rolloutByCreateOperation(operationUid, false);
        if (replay != null) {
            requireRolloutReplay(replay, request, targets, batchSize, reason);
            return rolloutView(replay, true);
        }
        ReleaseRow release = requireRelease(request.releaseUid(), true);
        RolloutRow concurrentReplay = rolloutByCreateOperation(
                operationUid, false);
        if (concurrentReplay != null) {
            requireRolloutReplay(
                    concurrentReplay, request, targets, batchSize, reason);
            return rolloutView(concurrentReplay, true);
        }
        if (!"READY".equals(release.status()) || release.declarationId() == null) {
            throw conflict(
                    "DEVICE.BUSINESS_RELEASE_NOT_READY",
                    "只有已经完成校验和批准、且当前未暂停的发布可以创建计划");
        }
        Map<String, EligibleDevice> eligibleByHardwareSn = new LinkedHashMap<>();
        targets.stream().sorted().forEach(hardwareSn ->
                eligibleByHardwareSn.put(
                        hardwareSn,
                        requireEligibleDevice(hardwareSn, release, null)));
        List<EligibleDevice> eligible = new ArrayList<>();
        for (String hardwareSn : targets) {
            eligible.add(eligibleByHardwareSn.get(hardwareSn));
        }
        long adminId = platformAdminId(actor);
        LocalDateTime now = databaseNow();
        UUID rolloutUid = UUID.randomUUID();
        int waveDeviceCount = eligible.size() - 1;
        int maximumWave = waveDeviceCount == 0
                ? 0 : (waveDeviceCount + batchSize - 1) / batchSize;
        KeyHolder key = new GeneratedKeyHolder();
        try {
            jdbc.update(connection -> {
                var statement = connection.prepareStatement("""
                        INSERT INTO dev_edge_software_rollout (
                            rollout_uid, create_operation_uid,
                            release_control_id, rollout_status,
                            validation_asset_id, batch_size,
                            maximum_wave_no, current_wave_no,
                            observation_window_seconds,
                            download_timeout_seconds, drain_timeout_seconds,
                            maximum_retry_count,
                            remote_dispatch_enabled_snapshot,
                            change_reason, created_by_platform_admin_id,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, 'DRAFT', ?, ?, ?, -1,
                                  ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, java.sql.Statement.RETURN_GENERATED_KEYS);
                statement.setString(1, rolloutUid.toString());
                statement.setString(2, operationUid.toString());
                statement.setLong(3, release.id());
                statement.setLong(4, eligible.getFirst().assetId());
                statement.setInt(5, batchSize);
                statement.setInt(6, maximumWave);
                statement.setInt(7, POLICY_SECONDS);
                statement.setInt(8, POLICY_SECONDS);
                statement.setInt(9, POLICY_SECONDS);
                statement.setInt(10, MAXIMUM_RETRIES);
                statement.setBoolean(11, remoteDispatchEnabled);
                statement.setString(12, reason);
                statement.setLong(13, adminId);
                statement.setObject(14, now);
                statement.setObject(15, now);
                return statement;
            }, key);
        } catch (DuplicateKeyException exception) {
            throw idempotencyConflict(
                    "同一幂等键已经用于另一项业务更新计划");
        }
        long rolloutId = requireGeneratedId(key, "business rollout");
        for (int index = 0; index < eligible.size(); index++) {
            EligibleDevice device = eligible.get(index);
            String kind = index == 0 ? "VALIDATION" : "WAVE";
            int waveNo = index == 0 ? 0 : ((index - 1) / batchSize) + 1;
            jdbc.update("""
                    INSERT INTO dev_edge_software_deployment (
                        deployment_uid, rollout_id, release_id, asset_id,
                        tenant_id, organization_id,
                        deployment_kind, wave_no, deployment_status,
                        eligibility_status, eligibility_snapshot,
                        eligibility_sha256, source_software_fact_id,
                        source_management_state_sequence,
                        source_business_baseline_kind,
                        source_business_release_uid,
                        source_business_release_sequence,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PLANNED',
                              'ELIGIBLE', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    UUID.randomUUID().toString(),
                    rolloutId,
                    release.declarationId(),
                    device.assetId(),
                    device.tenantId(),
                    device.organizationId(),
                    kind,
                    waveNo,
                    device.snapshotJson(),
                    device.snapshotSha256(),
                    device.softwareFactId(),
                    device.managementStateSequence(),
                    device.baselineKind(),
                    device.activeReleaseUid() == null
                            ? null : device.activeReleaseUid().toString(),
                    device.activeReleaseSequence(),
                    now,
                    now);
        }
        insertRolloutAction(
                operationUid,
                rolloutId,
                "CREATE",
                adminId,
                reason,
                "DRAFT",
                now);
        return rolloutView(requireRollout(rolloutUid, false), true);
    }

    @Transactional(readOnly = true)
    public PageData<RolloutView> listRollouts(int page, int pageSize) {
        authorizePlatform();
        int safePage = page(page);
        int safeSize = pageSize(pageSize);
        long total = jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_edge_software_rollout",
                Long.class);
        List<RolloutView> items = jdbc.query(
                ROLLOUT_SELECT
                        + " ORDER BY rollout.created_at DESC, rollout.id DESC"
                        + " LIMIT ? OFFSET ?",
                (rs, ignored) -> rolloutView(rolloutRow(rs), false),
                safeSize,
                (safePage - 1) * safeSize);
        return new PageData<>(items, safePage, safeSize, total);
    }

    @Transactional(readOnly = true)
    public RolloutView rolloutDetail(UUID rolloutUid) {
        authorizePlatform();
        requireUuidV4(rolloutUid, "rolloutUid");
        return rolloutView(requireRollout(rolloutUid, false), true);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public RolloutView stopRollout(
            UUID operationUid,
            UUID rolloutUid,
            String reason) {
        requireUuidV4(operationUid, "Idempotency-Key");
        requireUuidV4(rolloutUid, "rolloutUid");
        String normalizedReason = requiredReason(reason);
        AuthorizedDeviceScope actor = authorizePlatform();
        TargetWebAuditRequestContext.describe(
                "device.business-release.rollout.stop", rolloutUid.toString());
        RolloutRow rollout = requireRollout(rolloutUid, true);
        RolloutActionRow replay = rolloutAction(operationUid);
        if (replay != null) {
            requireRolloutActionReplay(
                    replay, rollout.id(), "STOP", normalizedReason);
            return rolloutView(requireRollout(rolloutUid, false), true);
        }
        if (!"DRAFT".equals(rollout.status())) {
            throw invalidTransition("只有尚未下发的灰度计划可以停止");
        }
        long adminId = platformAdminId(actor);
        LocalDateTime now = databaseNow();
        jdbc.update("""
                UPDATE dev_edge_software_rollout
                SET rollout_status = 'STOPPED',
                    stopped_by_platform_admin_id = ?,
                    stopped_at = ?, stop_reason = ?,
                    updated_at = ?, lock_version = lock_version + 1
                WHERE id = ? AND rollout_status = 'DRAFT'
                """, adminId, now, normalizedReason, now, rollout.id());
        insertRolloutAction(
                operationUid,
                rollout.id(),
                "STOP",
                adminId,
                normalizedReason,
                "STOPPED",
                now);
        return rolloutView(requireRollout(rolloutUid, false), true);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public RolloutView startValidation(
            UUID operationUid,
            UUID rolloutUid,
            String reason) {
        requireUuidV4(operationUid, "Idempotency-Key");
        requireUuidV4(rolloutUid, "rolloutUid");
        String normalizedReason = requiredReason(reason);
        AuthorizedDeviceScope actor = authorizePlatform();
        TargetWebAuditRequestContext.describe(
                "device.business-release.rollout.validation-start",
                rolloutUid.toString());
        RolloutRow rollout = requireRollout(rolloutUid, true);
        RolloutActionRow replay = rolloutAction(operationUid);
        if (replay != null) {
            requireRolloutActionReplay(
                    replay,
                    rollout.id(),
                    "START_VALIDATION",
                    normalizedReason);
            return rolloutView(requireRollout(rolloutUid, false), true);
        }
        if (!remoteDispatchEnabled || !rollout.remoteDispatchEnabled()) {
            throw conflict(
                    "DEVICE.BUSINESS_REMOTE_DISPATCH_DISABLED",
                    "后端或该计划创建时未开启真实远程下发，不能向设备发送更新");
        }
        if (!"DRAFT".equals(rollout.status())) {
            throw invalidTransition("只有尚未下发的计划可以开始单设备验证");
        }
        requireDispatchDependencies();
        BusinessReleaseArtifactStoragePort.Readiness artifactReadiness =
                artifacts.readiness();
        if (!artifactReadiness.available()) {
            throw conflict(
                    "DEVICE.BUSINESS_RELEASE_STORAGE_NOT_READY",
                    artifactReadiness.message());
        }
        BusinessReleaseSigningKeyPort.Readiness signingReadiness =
                signingKeys.readiness();
        if (!signingReadiness.available()) {
            throw conflict(
                    "DEVICE.BUSINESS_RELEASE_SIGNING_NOT_READY",
                    signingReadiness.message());
        }
        ReleaseRow release = releaseById(rollout.releaseControlId());
        if (!"READY".equals(release.status())
                || release.declarationId() == null
                || release.signatureBytes() == null
                || release.signatureBytes().length != 64) {
            throw conflict(
                    "DEVICE.BUSINESS_RELEASE_NOT_READY",
                    "目标发布当前不具备可下发的签名制品事实");
        }
        DeploymentRow deployment = requireValidationDeployment(
                rollout.id(), true);
        if (!"PLANNED".equals(deployment.status())) {
            throw invalidTransition("验证设备已经下发或处理过本次更新");
        }
        EligibleDevice current = requireEligibleDevice(
                deployment.hardwareSn(), release, rollout.id());
        if (current.assetId() != deployment.assetId()) {
            throw new IllegalStateException(
                    "business validation deployment asset changed");
        }
        LocalDateTime now = databaseNow();
        UUID commandUid = UUID.randomUUID();
        UUID updateUid = UUID.randomUUID();
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("deploymentUid", deployment.uid().toString());
        payload.put("updateUid", updateUid.toString());
        payload.put("controlSequence", 1L);
        payload.put("releaseUid", release.uid().toString());
        payload.put("versionName", release.versionName());
        payload.put("releaseSequence", release.releaseSequence());
        payload.put("objectKey", release.packageObjectKey());
        payload.put("packageSha256", release.packageSha256());
        payload.put("packageSize", release.packageSize());
        payload.put(
                "packageSignatureBase64",
                Base64.getEncoder().encodeToString(release.signatureBytes()));
        payload.put("signatureSha256", release.signatureSha256());
        payload.put("signingKeyId", release.signingKeyId());
        payload.put(
                "observationWindowSeconds", rollout.observationSeconds());
        payload.put("downloadTimeoutSeconds", rollout.downloadSeconds());
        payload.put("drainTimeoutSeconds", rollout.drainSeconds());
        payload.put("maximumRetryCount", rollout.maximumRetryCount());
        payload.put("reason", normalizedReason);

        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", COMMAND_TYPE);
        envelope.put("targetDeviceName", deployment.hardwareSn());
        envelope.put("target", Map.of(
                "type", TARGET_TYPE,
                "uid", deployment.uid().toString()));
        envelope.put("issuedAt", timestamp(now));
        envelope.put("expiresAt", timestamp(now.plusMinutes(5)));
        envelope.put("payloadSchemaVersion", 2);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(canonicalizer.payloadSha256(payload)));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        envelope.put("downloadGrant", null);
        byte[] envelopeSha256 = canonicalizer.payloadSha256(envelope);
        UUID taskUid = tasks.register(
                new ReliablePlatformDeviceControlTaskRegistration(
                        COMMAND_TYPE,
                        COMMAND_TYPE + ":"
                                + deployment.uid().toString()
                                .toUpperCase(Locale.ROOT),
                        TARGET_TYPE,
                        deployment.uid().toString(),
                        taskRefFactory.issue(deployment.assetId()),
                        2,
                        objectMapper.writeValueAsString(envelope),
                        envelopeSha256,
                        rollout.uid(),
                        commandUid,
                        20));
        int updated = jdbc.update("""
                UPDATE dev_edge_software_deployment
                SET deployment_status = 'QUEUED', command_uid = ?,
                    reliable_task_uid = ?, edge_update_uid = ?,
                    control_sequence = 1, business_admission_state = 'LOCKED',
                    queued_at = ?, updated_at = ?, lock_version = lock_version + 1
                WHERE id = ? AND deployment_status = 'PLANNED'
                """,
                commandUid.toString(),
                taskUid.toString(),
                updateUid.toString(),
                now,
                now,
                deployment.id());
        if (updated != 1) {
            throw new IllegalStateException(
                    "business validation dispatch lost its deployment lock");
        }
        jdbc.update("""
                UPDATE dev_device_compatibility_projection
                SET business_admission_status = 'PAUSED',
                    lock_version = lock_version + 1, updated_at = ?
                WHERE asset_id = ?
                """, now, deployment.assetId());
        jdbc.update("""
                UPDATE dev_edge_software_rollout
                SET rollout_status = 'VALIDATING', current_wave_no = 0,
                    updated_at = ?, lock_version = lock_version + 1
                WHERE id = ? AND rollout_status = 'DRAFT'
                """, now, rollout.id());
        insertRolloutAction(
                operationUid,
                rollout.id(),
                "START_VALIDATION",
                platformAdminId(actor),
                normalizedReason,
                "VALIDATING",
                now);
        return rolloutView(requireRollout(rolloutUid, false), true);
    }

    /** Requests cancellation; only the later device result makes it final. */
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public RolloutView cancelDeployment(
            UUID operationUid,
            UUID rolloutUid,
            UUID deploymentUid,
            String reason) {
        requireUuidV4(operationUid, "Idempotency-Key");
        requireUuidV4(rolloutUid, "rolloutUid");
        requireUuidV4(deploymentUid, "deploymentUid");
        String normalizedReason = requiredReason(reason);
        AuthorizedDeviceScope actor = authorizePlatform();
        long adminId = platformAdminId(actor);
        TargetWebAuditRequestContext.describe(
                "device.business-release.deployment.cancel-request",
                deploymentUid.toString());
        requireDispatchDependencies();

        DeploymentRow deployment = requireDeployment(deploymentUid, true);
        RolloutRow rollout = requireRollout(rolloutUid, true);
        if (deployment.rolloutId() != rollout.id()
                || !"VALIDATION".equals(deployment.kind())) {
            throw notFound("该计划中找不到这台验证设备的更新任务");
        }
        RolloutActionRow replay = rolloutAction(operationUid);
        if (replay != null) {
            requireRolloutActionReplay(
                    replay,
                    rollout.id(),
                    "REQUEST_CANCEL",
                    normalizedReason);
            if ("NONE".equals(deployment.cancellationStatus())
                    || !normalizedReason.equals(deployment.cancelReason())) {
                throw idempotencyConflict(
                        "同一幂等键不能取消另一项设备更新任务");
            }
            return rolloutView(requireRollout(rolloutUid, false), true);
        }
        if (!"VALIDATING".equals(rollout.status())) {
            throw invalidTransition("只有正在验证设备上的更新可以请求取消");
        }
        if (DEPLOYMENT_TERMINAL.contains(deployment.status())) {
            throw invalidTransition("设备更新已经结束，不能再请求取消");
        }
        if (!"NONE".equals(deployment.cancellationStatus())) {
            throw invalidTransition("该设备更新已经请求过取消，请等待设备确认结果");
        }
        if (deployment.commandUid() == null
                || deployment.taskUid() == null
                || deployment.edgeUpdateUid() == null
                || deployment.controlSequence() == null) {
            throw new IllegalStateException(
                    "dispatched business update identity is incomplete");
        }
        long cancelSequence;
        try {
            cancelSequence = Math.addExact(deployment.controlSequence(), 1L);
        } catch (ArithmeticException exception) {
            throw new IllegalStateException(
                    "business update control sequence is exhausted", exception);
        }
        if (cancelSequence > 9_007_199_254_740_991L) {
            throw new IllegalStateException(
                    "business update control sequence is exhausted");
        }

        LocalDateTime now = databaseNow();
        UUID commandUid = UUID.randomUUID();
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("deploymentUid", deployment.uid().toString());
        payload.put("updateUid", deployment.edgeUpdateUid().toString());
        payload.put("controlSequence", cancelSequence);
        payload.put("reason", normalizedReason);

        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", CANCEL_COMMAND_TYPE);
        envelope.put("targetDeviceName", deployment.hardwareSn());
        envelope.put("target", Map.of(
                "type", TARGET_TYPE,
                "uid", deployment.uid().toString()));
        envelope.put("issuedAt", timestamp(now));
        envelope.put("expiresAt", timestamp(now.plusMinutes(10)));
        envelope.put("payloadSchemaVersion", 2);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(canonicalizer.payloadSha256(payload)));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        byte[] envelopeSha256 = canonicalizer.payloadSha256(envelope);
        UUID taskUid = tasks.register(
                new ReliablePlatformDeviceControlTaskRegistration(
                        CANCEL_COMMAND_TYPE,
                        CANCEL_COMMAND_TYPE + ":"
                                + deployment.uid().toString()
                                .toUpperCase(Locale.ROOT),
                        TARGET_TYPE,
                        deployment.uid().toString(),
                        taskRefFactory.issue(deployment.assetId()),
                        2,
                        objectMapper.writeValueAsString(envelope),
                        envelopeSha256,
                        rollout.uid(),
                        commandUid,
                        20));
        int updated = jdbc.update("""
                UPDATE dev_edge_software_deployment
                SET cancel_command_uid = ?, cancel_reliable_task_uid = ?,
                    cancel_control_sequence = ?, cancellation_status = 'QUEUED',
                    cancel_reason = ?,
                    cancel_requested_by_platform_admin_id = ?,
                    cancel_requested_at = ?, updated_at = ?,
                    lock_version = lock_version + 1
                WHERE id = ? AND cancellation_status = 'NONE'
                  AND deployment_status NOT IN (
                      'SUCCEEDED', 'ROLLED_BACK', 'DEFERRED', 'REJECTED',
                      'FAILED_LOCKED', 'CANCELLED', 'LOCAL_CANCELLED'
                  )
                """,
                commandUid.toString(),
                taskUid.toString(),
                cancelSequence,
                normalizedReason,
                adminId,
                now,
                now,
                deployment.id());
        if (updated != 1) {
            throw new IllegalStateException(
                    "business cancellation lost its deployment lock");
        }
        insertRolloutAction(
                operationUid,
                rollout.id(),
                "REQUEST_CANCEL",
                adminId,
                normalizedReason,
                "VALIDATING",
                now);
        return rolloutView(requireRollout(rolloutUid, false), true);
    }

    private void requireDispatchDependencies() {
        if (objectMapper == null
                || taskRefFactory == null
                || tasks == null
                || taskProof == null
                || taskWake == null
                || compatibility == null) {
            throw new IllegalStateException(
                    "business runtime dispatch dependencies are unavailable");
        }
    }

    /** Applies one authenticated updater progress fact without inferring install state. */
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public TrustedDeviceEventApplyResult applyProgress(
            TrustedPlatformDeviceAssetFactEvent inboxEvent) {
        if (!EVENT_TYPE.equals(inboxEvent.messageKind())) {
            throw new IllegalArgumentException(
                    "unsupported business runtime progress event");
        }
        requireDispatchDependencies();
        return inboxEvent.sourceInbox().use(sourceInboxId -> {
            JsonNode normalized = objectMapper.readTree(
                    inboxEvent.normalizedPayload());
            JsonNode source = jsonObject(normalized, "trustedSource");
            JsonNode event = jsonObject(normalized, "event");
            JsonNode target = jsonObject(event, "target");
            JsonNode payload = jsonObject(event, "payload");
            requireJsonText(event, "eventType", EVENT_TYPE);
            requireJsonText(target, "type", TARGET_TYPE);
            UUID eventUid = jsonUuid(event, "eventUid");
            if (businessProgressExists(eventUid, sourceInboxId)) {
                return TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
            }
            UUID deploymentUid = jsonUuid(payload, "deploymentUid");
            if (!deploymentUid.equals(jsonUuid(target, "uid"))) {
                throw new IllegalArgumentException(
                        "business runtime progress target differs from deployment");
            }
            DeploymentRow deployment = requireDeployment(deploymentUid, true);
            ReleaseRow release = releaseById(deployment.releaseControlId());
            if (!deployment.hardwareSn().equals(
                    jsonText(source, "deviceName", 64))) {
                throw new IllegalArgumentException(
                        "business runtime progress source differs from deployment asset");
            }
            if (!release.uid().equals(jsonUuid(payload, "releaseUid"))
                    || !release.versionName().equals(
                    jsonText(payload, "versionName", 32))
                    || release.releaseSequence()
                    != jsonLong(payload, "releaseSequence", 1,
                    9_007_199_254_740_991L)
                    || !release.packageSha256().equals(
                    jsonPattern(payload, "packageSha256", SHA256, 64))) {
                throw new IllegalArgumentException(
                        "business runtime progress differs from frozen release identity");
            }
            UUID commandUid = nullableJsonUuid(event, "commandUid");
            if (commandUid == null
                    || !commandUid.equals(deployment.commandUid())) {
                throw new IllegalArgumentException(
                        "business runtime progress command differs from deployment");
            }
            UUID updateUid = jsonUuid(payload, "updateUid");
            if (!updateUid.equals(deployment.edgeUpdateUid())) {
                throw new IllegalArgumentException(
                        "business runtime progress update identity changed");
            }
            String stage = jsonText(payload, "stage", 40);
            if (!isBusinessProgressStage(stage)) {
                throw new IllegalArgumentException(
                        "business runtime progress stage is unsupported");
            }
            long stageSequence = jsonLong(
                    payload, "stageSequence", 1, 9_007_199_254_740_991L);
            String admission = jsonText(
                    payload, "businessAdmissionState", 16);
            if (!Set.of("OPEN", "DRAINING", "MAINTENANCE", "LOCKED")
                    .contains(admission)) {
                throw new IllegalArgumentException(
                        "business runtime admission state is unsupported");
            }
            int downloadAttempts = Math.toIntExact(jsonLong(
                    payload, "downloadAttemptCount", 0, 10));
            int targetAttempts = Math.toIntExact(jsonLong(
                    payload, "targetAttemptCount", 0, 10));
            int rollbackAttempts = Math.toIntExact(jsonLong(
                    payload, "rollbackAttemptCount", 0, 10));
            boolean databaseRestored = jsonBoolean(
                    payload, "databaseRestored");
            String errorCode = nullableJsonPattern(
                    payload,
                    "errorCode",
                    "^[A-Z][A-Z0-9_]{0,63}$",
                    64);
            if (ERROR_STAGES.contains(stage) != (errorCode != null)) {
                throw new IllegalArgumentException(
                        "business runtime failure code differs from stage");
            }
            InstalledBusiness installed = installedBusiness(payload);
            if ("SUCCEEDED".equals(stage)
                    && (installed == null
                    || !release.uid().equals(installed.releaseUid())
                    || !release.versionName().equals(installed.versionName())
                    || release.releaseSequence()
                    != installed.releaseSequence()
                    || !release.packageSha256().equals(
                    installed.packageSha256()))) {
                throw new IllegalArgumentException(
                        "successful business progress does not prove target identity");
            }
            if ("SUCCEEDED".equals(stage) && databaseRestored) {
                throw new IllegalArgumentException(
                        "successful business progress cannot restore the previous database");
            }
            if ("ROLLED_BACK".equals(stage)) {
                SourceBaseline frozenSource = sourceBaseline(
                        deployment.id());
                boolean restoredIdentityMatches = IMAGE_BRIDGE_BASELINE.equals(
                        frozenSource.kind())
                        ? installed == null
                        : frozenSource.release() != null
                        && frozenSource.release().equals(installed);
                if (!restoredIdentityMatches
                        || !databaseRestored) {
                    throw new IllegalArgumentException(
                            "rolled back business progress does not prove the frozen source identity and database restore");
                }
            }
            String payloadSha256 = jsonPattern(
                    event, "payloadSha256", SHA256, 64);
            LocalDateTime now = databaseNow();
            LocalDateTime occurredAt = nullableJsonInstant(
                    event, "occurredAt");
            try {
                jdbc.update("""
                        INSERT INTO dev_edge_software_deployment_progress (
                            event_uid, source_inbox_id, deployment_id,
                            edge_update_uid, stage, stage_sequence,
                            business_admission_state,
                            download_attempt_count, target_attempt_count,
                            rollback_attempt_count, installed_release_uid,
                            installed_version_name,
                            installed_release_sequence,
                            installed_package_sha256, database_restored,
                            error_code, payload_sha256, normalized_payload,
                            occurred_at, received_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                  ?, ?, ?, ?, ?, ?, ?)
                        """,
                        eventUid.toString(),
                        sourceInboxId,
                        deployment.id(),
                        updateUid.toString(),
                        stage,
                        stageSequence,
                        admission,
                        downloadAttempts,
                        targetAttempts,
                        rollbackAttempts,
                        installed == null
                                ? null : installed.releaseUid().toString(),
                        installed == null ? null : installed.versionName(),
                        installed == null ? null : installed.releaseSequence(),
                        installed == null ? null : HexFormat.of().parseHex(
                                installed.packageSha256()),
                        databaseRestored,
                        errorCode,
                        HexFormat.of().parseHex(payloadSha256),
                        inboxEvent.normalizedPayload(),
                        occurredAt,
                        now,
                        now);
            } catch (DuplicateKeyException duplicate) {
                throw new IllegalArgumentException(
                        "business runtime progress reuses a frozen identity",
                        duplicate);
            }
            if ("CANCELLED".equals(stage)) {
                if (!Set.of("QUEUED", "CANCELLED")
                        .contains(deployment.cancellationStatus())
                        || !"OPEN".equals(admission)) {
                    throw new IllegalArgumentException(
                            "business cancellation progress has no matching safe cancellation request");
                }
                taskProof.completeFromTrustedProof(
                        COMMAND_TYPE,
                        TARGET_TYPE,
                        deployment.uid().toString());
                return TrustedDeviceEventApplyResult.APPLIED;
            }
            if (DEPLOYMENT_TERMINAL.contains(deployment.status())) {
                return TrustedDeviceEventApplyResult.APPLIED;
            }
            boolean advances = stageSequence > deployment.stageSequence();
            if (advances && (downloadAttempts < deployment.downloadAttemptCount()
                    || targetAttempts < deployment.targetAttemptCount()
                    || rollbackAttempts < deployment.rollbackAttemptCount())) {
                throw new IllegalArgumentException(
                        "business runtime attempt counters regressed");
            }
            if (!advances) {
                return TrustedDeviceEventApplyResult.APPLIED;
            }
            LocalDateTime completedAt = DEPLOYMENT_TERMINAL.contains(stage)
                    ? now : null;
            int updated = jdbc.update("""
                    UPDATE dev_edge_software_deployment
                    SET deployment_status = ?, stage_sequence = ?,
                        business_admission_state = ?,
                        download_attempt_count = ?, target_attempt_count = ?,
                        rollback_attempt_count = ?, installed_release_uid = ?,
                        installed_version_name = ?,
                        installed_release_sequence = ?,
                        installed_package_sha256 = ?, database_restored = ?,
                        error_code = ?, last_event_uid = ?, completed_at = ?,
                        updated_at = ?, lock_version = lock_version + 1
                    WHERE id = ?
                      AND deployment_status NOT IN (
                          'SUCCEEDED', 'ROLLED_BACK', 'DEFERRED',
                          'REJECTED', 'FAILED_LOCKED', 'CANCELLED', 'LOCAL_CANCELLED'
                      )
                    """,
                    stage,
                    stageSequence,
                    admission,
                    downloadAttempts,
                    targetAttempts,
                    rollbackAttempts,
                    installed == null ? null : installed.releaseUid().toString(),
                    installed == null ? null : installed.versionName(),
                    installed == null ? null : installed.releaseSequence(),
                    installed == null ? null : HexFormat.of().parseHex(
                            installed.packageSha256()),
                    databaseRestored,
                    errorCode,
                    eventUid.toString(),
                    completedAt,
                    now,
                    deployment.id());
            if (updated != 1) {
                throw new IllegalStateException(
                        "business deployment progress lost its lock");
            }
            if ("DOWNLOAD_AUTHORIZATION_REQUIRED".equals(stage)) {
                taskWake.wake(new ReliableTaskWake(
                        deployment.taskUid(),
                        "BUSINESS_DOWNLOAD_AUTHORIZATION_REQUIRED"));
            }
            if (DEPLOYMENT_TERMINAL.contains(stage)) {
                taskProof.completeFromTrustedProof(
                        COMMAND_TYPE,
                        TARGET_TYPE,
                        deployment.uid().toString());
                if ("VALIDATION".equals(deployment.kind())
                        && "VALIDATING".equals(deployment.rolloutStatus())) {
                    jdbc.update("""
                            UPDATE dev_edge_software_rollout
                            SET rollout_status = CASE
                                    WHEN ? <> 'SUCCEEDED'
                                        THEN 'VALIDATION_FAILED'
                                    WHEN maximum_wave_no = 0
                                        THEN 'COMPLETED'
                                    ELSE 'AWAITING_PROMOTION'
                                END,
                                updated_at = ?,
                                lock_version = lock_version + 1
                            WHERE id = ? AND rollout_status = 'VALIDATING'
                            """,
                            stage,
                            now,
                            deployment.rolloutId());
                }
                compatibility.reassessLatestFact(
                        deployment.assetId(), now);
            }
            return TrustedDeviceEventApplyResult.APPLIED;
        });
    }

    /** Applies the device's authoritative safe-cancel or too-late result. */
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public TrustedDeviceEventApplyResult applyCancellationResult(
            TrustedPlatformDeviceAssetFactEvent inboxEvent) {
        if (!CANCEL_EVENT_TYPE.equals(inboxEvent.messageKind())) {
            throw new IllegalArgumentException(
                    "unsupported business runtime cancellation result");
        }
        requireDispatchDependencies();
        return inboxEvent.sourceInbox().use(sourceInboxId -> {
            JsonNode normalized = objectMapper.readTree(
                    inboxEvent.normalizedPayload());
            JsonNode source = jsonObject(normalized, "trustedSource");
            JsonNode event = jsonObject(normalized, "event");
            JsonNode target = jsonObject(event, "target");
            JsonNode payload = jsonObject(event, "payload");
            requireJsonText(event, "eventType", CANCEL_EVENT_TYPE);
            requireJsonText(target, "type", TARGET_TYPE);
            UUID eventUid = jsonUuid(event, "eventUid");
            if (businessCancelResultExists(eventUid, sourceInboxId)) {
                return TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
            }
            UUID deploymentUid = jsonUuid(payload, "deploymentUid");
            if (!deploymentUid.equals(jsonUuid(target, "uid"))) {
                throw new IllegalArgumentException(
                        "business cancellation target differs from deployment");
            }
            DeploymentRow deployment = requireDeployment(deploymentUid, true);
            if (!deployment.hardwareSn().equals(
                    jsonText(source, "deviceName", 64))) {
                throw new IllegalArgumentException(
                        "business cancellation source differs from deployment asset");
            }
            UUID commandUid = nullableJsonUuid(event, "commandUid");
            if (commandUid == null
                    || !commandUid.equals(deployment.cancelCommandUid())) {
                throw new IllegalArgumentException(
                        "business cancellation command differs from deployment");
            }
            UUID updateUid = jsonUuid(payload, "updateUid");
            if (!updateUid.equals(deployment.edgeUpdateUid())) {
                throw new IllegalArgumentException(
                        "business cancellation update identity changed");
            }
            long controlSequence = jsonLong(
                    payload, "controlSequence", 1,
                    9_007_199_254_740_991L);
            if (deployment.cancelControlSequence() == null
                    || controlSequence
                    != deployment.cancelControlSequence()) {
                throw new IllegalArgumentException(
                        "business cancellation control sequence differs from request");
            }
            String result = jsonText(payload, "result", 16);
            if (!Set.of("CANCELLED", "TOO_LATE").contains(result)) {
                throw new IllegalArgumentException(
                        "business cancellation result is unsupported");
            }
            String observedStage = jsonText(
                    payload, "observedStage", 40);
            if (!isBusinessCancellationObservedStage(observedStage)) {
                throw new IllegalArgumentException(
                        "business cancellation observed stage is unsupported");
            }
            String admission = jsonText(
                    payload, "businessAdmissionState", 16);
            if (!Set.of("OPEN", "DRAINING", "MAINTENANCE", "LOCKED")
                    .contains(admission)) {
                throw new IllegalArgumentException(
                        "business cancellation admission state is unsupported");
            }
            String errorCode = nullableJsonPattern(
                    payload,
                    "errorCode",
                    "^[A-Z][A-Z0-9_]{0,63}$",
                    64);
            if (("CANCELLED".equals(result)
                    && (errorCode != null || !"OPEN".equals(admission)))
                    || ("TOO_LATE".equals(result)
                    && !"BUSINESS_UPDATE_CANCEL_TOO_LATE"
                    .equals(errorCode))) {
                throw new IllegalArgumentException(
                        "business cancellation result facts are inconsistent");
            }
            if (!"QUEUED".equals(deployment.cancellationStatus())) {
                throw new IllegalArgumentException(
                        "business cancellation result has no pending request");
            }

            String payloadSha256 = jsonPattern(
                    event, "payloadSha256", SHA256, 64);
            LocalDateTime now = databaseNow();
            LocalDateTime occurredAt = nullableJsonInstant(
                    event, "occurredAt");
            try {
                jdbc.update("""
                        INSERT INTO dev_edge_software_deployment_cancel_result (
                            event_uid, source_inbox_id, deployment_id,
                            cancel_command_uid, edge_update_uid,
                            control_sequence, result, observed_stage,
                            business_admission_state, error_code,
                            payload_sha256, normalized_payload,
                            occurred_at, received_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        eventUid.toString(),
                        sourceInboxId,
                        deployment.id(),
                        commandUid.toString(),
                        updateUid.toString(),
                        controlSequence,
                        result,
                        observedStage,
                        admission,
                        errorCode,
                        HexFormat.of().parseHex(payloadSha256),
                        inboxEvent.normalizedPayload(),
                        occurredAt,
                        now,
                        now);
            } catch (DuplicateKeyException duplicate) {
                throw new IllegalArgumentException(
                        "business cancellation result reuses a frozen identity",
                        duplicate);
            }

            if ("TOO_LATE".equals(result)) {
                int updated = jdbc.update("""
                        UPDATE dev_edge_software_deployment
                        SET cancellation_status = 'TOO_LATE',
                            cancel_result_at = ?, updated_at = ?,
                            lock_version = lock_version + 1
                        WHERE id = ? AND cancellation_status = 'QUEUED'
                        """, now, now, deployment.id());
                if (updated != 1) {
                    throw new IllegalStateException(
                            "business too-late cancellation lost its deployment lock");
                }
                taskProof.completeFromTrustedProof(
                        CANCEL_COMMAND_TYPE,
                        TARGET_TYPE,
                        deployment.uid().toString());
                return TrustedDeviceEventApplyResult.APPLIED;
            }

            int updated = jdbc.update("""
                    UPDATE dev_edge_software_deployment
                    SET deployment_status = 'CANCELLED',
                        business_admission_state = 'OPEN',
                        cancellation_status = 'CANCELLED',
                        cancel_result_at = ?, error_code = NULL,
                        completed_at = ?, updated_at = ?,
                        lock_version = lock_version + 1
                    WHERE id = ? AND cancellation_status = 'QUEUED'
                      AND deployment_status NOT IN (
                          'SUCCEEDED', 'ROLLED_BACK', 'DEFERRED', 'REJECTED',
                          'FAILED_LOCKED', 'CANCELLED', 'LOCAL_CANCELLED'
                      )
                    """, now, now, now, deployment.id());
            if (updated != 1) {
                throw new IllegalStateException(
                        "safe business cancellation lost its deployment lock");
            }
            int rolloutUpdated = jdbc.update("""
                    UPDATE dev_edge_software_rollout
                    SET rollout_status = 'STOPPED', current_wave_no = -1,
                        stopped_by_platform_admin_id = ?, stopped_at = ?,
                        stop_reason = ?, updated_at = ?,
                        lock_version = lock_version + 1
                    WHERE id = ? AND rollout_status = 'VALIDATING'
                    """,
                    deployment.cancelRequestedByPlatformAdminId(),
                    now,
                    deployment.cancelReason(),
                    now,
                    deployment.rolloutId());
            if (rolloutUpdated != 1) {
                throw new IllegalStateException(
                        "safe business cancellation lost its rollout lock");
            }
            compatibility.reassessLatestFact(
                    deployment.assetId(), now);
            taskProof.completeFromTrustedProof(
                    CANCEL_COMMAND_TYPE,
                    TARGET_TYPE,
                    deployment.uid().toString());
            taskProof.completeFromTrustedProof(
                    COMMAND_TYPE,
                    TARGET_TYPE,
                    deployment.uid().toString());
            return TrustedDeviceEventApplyResult.APPLIED;
        });
    }

    private ReleaseView completeVerification(
            UUID operationUid,
            UUID releaseUid,
            long adminId,
            String reason,
            VerifiedRelease verified) {
        return transactions.execute(status -> {
            ReleaseRow release = requireRelease(releaseUid, true);
            if (!"VERIFYING".equals(release.status())
                    || !operationUid.equals(
                    release.verificationOperationUid())) {
                return releaseView(release, true);
            }
            byte[] declarationSha256 = declarationSha256(verified);
            LocalDateTime declarationCreatedAt = databaseNow();
            KeyHolder key = new GeneratedKeyHolder();
            try {
                jdbc.update(connection -> {
                    var statement = connection.prepareStatement("""
                            INSERT INTO dev_edge_software_release (
                                release_uid, version_name, release_sequence,
                                package_sha256, package_format_version,
                                backend_command_contract_version,
                                device_event_contract_version,
                                communication_business_protocol_major,
                                communication_business_protocol_minor,
                                updater_business_protocol_major,
                                updater_business_protocol_minor,
                                uart_protocol_family,
                                uart_protocol_major, uart_protocol_minor,
                                required_fixed_frame_revision,
                                required_mcu_capability_bitmap_hex,
                                provided_business_capability_bitmap_hex,
                                declaration_sha256, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                      ?, ?, ?, ?, ?, ?, ?, ?)
                            """, java.sql.Statement.RETURN_GENERATED_KEYS);
                    int index = 1;
                    statement.setString(index++, verified.releaseUid().toString());
                    statement.setString(index++, verified.versionName());
                    statement.setLong(index++, verified.releaseSequence());
                    statement.setBytes(index++, HexFormat.of().parseHex(
                            verified.packageSha256()));
                    statement.setInt(index++, verified.packageFormatVersion());
                    statement.setInt(index++, verified.backendCommandContractVersion());
                    statement.setInt(index++, verified.deviceEventContractVersion());
                    statement.setInt(index++, verified.communicationBusinessProtocolMajor());
                    statement.setInt(index++, verified.communicationBusinessProtocolMinor());
                    statement.setInt(index++, verified.updaterBusinessProtocolMajor());
                    statement.setInt(index++, verified.updaterBusinessProtocolMinor());
                    statement.setString(index++, verified.uartProtocolFamily());
                    setNullableInteger(statement, index++, verified.uartProtocolMajor());
                    setNullableInteger(statement, index++, verified.uartProtocolMinor());
                    setNullableInteger(
                            statement, index++, verified.requiredFixedFrameRevision());
                    statement.setString(
                            index++, verified.requiredMcuCapabilityBitmapHex());
                    statement.setString(
                            index++, verified.providedBusinessCapabilityBitmapHex());
                    statement.setBytes(index++, declarationSha256);
                    statement.setObject(index, declarationCreatedAt);
                    return statement;
                }, key);
            } catch (DuplicateKeyException exception) {
                throw new VerificationException(
                        "DECLARATION_CONFLICT",
                        "该版本、发布顺序、制品摘要或兼容声明已经由其他发布登记");
            }
            long declarationId = requireGeneratedId(key, "business declaration");
            LocalDateTime now = databaseNow();
            int updated = jdbc.update("""
                    UPDATE dev_edge_software_release_control
                    SET release_status = 'AWAITING_APPROVAL',
                        verification_operation_uid = NULL,
                        declaration_id = ?,
                        verified_by_platform_admin_id = ?, verified_at = ?,
                        verification_error_code = NULL,
                        verification_error_message = NULL,
                        updated_at = ?, lock_version = lock_version + 1
                    WHERE id = ? AND release_status = 'VERIFYING'
                      AND verification_operation_uid = ?
                    """, declarationId, adminId, now, now, release.id(),
                    operationUid.toString());
            if (updated != 1) {
                throw new IllegalStateException(
                        "business release verification lease changed");
            }
            insertReleaseAction(
                    derivedOperationUid(operationUid, "passed"),
                    release.id(),
                    "VERIFICATION_PASSED",
                    adminId,
                    reason,
                    "AWAITING_APPROVAL",
                    now);
            return releaseView(requireRelease(releaseUid, false), true);
        });
    }

    private ReleaseView failVerification(
            UUID operationUid,
            UUID releaseUid,
            long adminId,
            String reason,
            String errorCode,
            String errorMessage) {
        return transactions.execute(status -> {
            ReleaseRow release = requireRelease(releaseUid, true);
            if (!"VERIFYING".equals(release.status())
                    || !operationUid.equals(
                    release.verificationOperationUid())) {
                return releaseView(release, true);
            }
            LocalDateTime now = databaseNow();
            String safeMessage = truncate(
                    errorMessage == null ? "发布包校验失败" : errorMessage,
                    500);
            int updated = jdbc.update("""
                    UPDATE dev_edge_software_release_control
                    SET release_status = 'VERIFICATION_FAILED',
                        verification_operation_uid = NULL,
                        verification_error_code = ?,
                        verification_error_message = ?,
                        updated_at = ?, lock_version = lock_version + 1
                    WHERE id = ? AND release_status = 'VERIFYING'
                      AND verification_operation_uid = ?
                    """, truncate(errorCode, 64), safeMessage, now, release.id(),
                    operationUid.toString());
            if (updated != 1) {
                throw new IllegalStateException(
                        "business release verification lease changed");
            }
            insertReleaseAction(
                    derivedOperationUid(operationUid, "failed"),
                    release.id(),
                    "VERIFICATION_FAILED",
                    adminId,
                    reason + "；" + safeMessage,
                    "VERIFICATION_FAILED",
                    now);
            return releaseView(requireRelease(releaseUid, false), true);
        });
    }

    private ReleaseView transitionRelease(
            UUID operationUid,
            UUID releaseUid,
            String reason,
            String expectedStatus,
            String targetStatus,
            String action,
            String auditAction) {
        requireUuidV4(operationUid, "Idempotency-Key");
        requireUuidV4(releaseUid, "releaseUid");
        String normalizedReason = requiredReason(reason);
        AuthorizedDeviceScope actor = authorizePlatform();
        TargetWebAuditRequestContext.describe(auditAction, releaseUid.toString());
        ReleaseRow release = requireRelease(releaseUid, true);
        ReleaseActionRow replay = releaseAction(operationUid);
        if (replay != null) {
            requireActionReplay(
                    replay, release.id(), action, normalizedReason);
            return releaseView(requireRelease(releaseUid, false), true);
        }
        if (!expectedStatus.equals(release.status())) {
            throw invalidTransition(
                    "当前发布状态不允许执行“" + actionLabel(action) + "”");
        }
        long adminId = platformAdminId(actor);
        LocalDateTime now = databaseNow();
        if ("APPROVE".equals(action)) {
            jdbc.update("""
                    UPDATE dev_edge_software_release_control
                    SET release_status = 'READY',
                        approved_by_platform_admin_id = ?, approved_at = ?,
                        updated_at = ?, lock_version = lock_version + 1
                    WHERE id = ? AND release_status = 'AWAITING_APPROVAL'
                    """, adminId, now, now, release.id());
        } else if ("SUSPEND".equals(action)) {
            jdbc.update("""
                    UPDATE dev_edge_software_release_control
                    SET release_status = 'SUSPENDED',
                        suspended_by_platform_admin_id = ?, suspended_at = ?,
                        suspension_reason = ?,
                        updated_at = ?, lock_version = lock_version + 1
                    WHERE id = ? AND release_status = 'READY'
                    """, adminId, now, normalizedReason, now, release.id());
        } else if ("RESUME".equals(action)) {
            jdbc.update("""
                    UPDATE dev_edge_software_release_control
                    SET release_status = 'READY',
                        updated_at = ?, lock_version = lock_version + 1
                    WHERE id = ? AND release_status = 'SUSPENDED'
                    """, now, release.id());
        } else {
            throw new IllegalStateException("unsupported release transition");
        }
        insertReleaseAction(
                operationUid,
                release.id(),
                action,
                adminId,
                normalizedReason,
                targetStatus,
                now);
        return releaseView(requireRelease(releaseUid, false), true);
    }

    private EligibleDevice requireEligibleDevice(
            String hardwareSn,
            ReleaseRow target,
            Long excludedRolloutId) {
        List<Long> locked = jdbc.query(
                "SELECT id FROM dev_device_asset WHERE hardware_sn = ? FOR UPDATE",
                (rs, ignored) -> rs.getLong(1),
                hardwareSn);
        if (locked.isEmpty()) {
            throw ineligible(hardwareSn, "平台中找不到这台设备");
        }
        List<Long> projectionLocks = jdbc.query("""
                SELECT asset_id
                FROM dev_device_compatibility_projection
                WHERE asset_id = ?
                FOR UPDATE
                """, (rs, ignored) -> rs.getLong(1), locked.getFirst());
        if (projectionLocks.size() != 1) {
            throw ineligible(hardwareSn, "设备的当前兼容性状态尚未建立");
        }
        List<DeviceCandidate> rows = jdbc.query(DEVICE_ELIGIBILITY_SELECT,
                (rs, ignored) -> new DeviceCandidate(
                        rs.getLong("asset_id"),
                        rs.getString("hardware_sn"),
                        nullableLong(rs, "tenant_id"),
                        nullableLong(rs, "organization_id"),
                        rs.getString("lifecycle_status"),
                        rs.getString("acceptance_status"),
                        rs.getString("architecture_generation"),
                        rs.getString("compatibility_status"),
                        rs.getString("business_admission_status"),
                        nullableLong(rs, "software_fact_id"),
                        nullableLong(rs, "management_state_sequence"),
                        rs.getString("business_gate_state"),
                        rs.getString("communication_business_protocol"),
                        rs.getString("updater_business_protocol"),
                        nullableInteger(rs, "business_package_format_version"),
                        rs.getString("active_business_release_uid"),
                        nullableLong(rs, "active_business_release_sequence"),
                        rs.getString("business_process_state"),
                        nullableBoolean(rs, "business_process_ready"),
                        rs.getString("negotiated_communication_business"),
                        rs.getString("negotiated_updater_business"),
                        nullableInteger(rs, "mcu_fixed_frame_revision"),
                        rs.getString("uart_state"),
                        rs.getString("uart_protocol_family"),
                        nullableInteger(rs, "uart_protocol_major"),
                        nullableInteger(rs, "uart_protocol_minor"),
                        rs.getString("capability_bitmap_hex")),
                hardwareSn);
        if (rows.size() != 1) {
            throw ineligible(hardwareSn, "设备的软件实际状态尚未完整上报");
        }
        DeviceCandidate device = rows.getFirst();
        List<String> reasons = new ArrayList<>();
        if (!"NORMAL".equals(device.lifecycleStatus())) {
            reasons.add("设备当前不是正常状态");
        }
        if (!"PASSED".equals(device.acceptanceStatus())) {
            reasons.add("设备尚未通过机器验收");
        }
        if (!"PERMANENT_V1".equals(device.architectureGeneration())) {
            reasons.add("设备尚未切换到常驻通信代理和设备更新器");
        }
        if (!COMPATIBLE.contains(device.compatibilityStatus())
                || !"ACCEPTING".equals(device.businessAdmissionStatus())) {
            reasons.add("设备当前兼容性或业务准入状态不允许升级");
        }
        if (device.softwareFactId() == null
                || device.managementStateSequence() == null) {
            reasons.add("设备缺少当前软件实际状态证据");
        }
        if (!"OPEN".equals(device.businessGateState())
                || !"RUNNING".equals(device.businessProcessState())
                || !Boolean.TRUE.equals(device.businessProcessReady())) {
            reasons.add("设备业务程序当前没有正常运行并开放新作业");
        }
        boolean imageBridge = device.activeReleaseUid() == null
                && device.activeReleaseSequence() == null;
        if ((device.activeReleaseUid() == null)
                != (device.activeReleaseSequence() == null)) {
            reasons.add("设备当前业务版本身份不完整");
        } else if (!imageBridge
                && device.activeReleaseSequence() >= target.releaseSequence()) {
            reasons.add("目标版本必须晚于设备实际安装的版本");
        }
        String targetCommunication = target.communicationMajor()
                + "." + target.communicationMinor();
        String targetUpdater = target.updaterMajor()
                + "." + target.updaterMinor();
        if (!targetCommunication.equals(device.communicationBusinessProtocol())
                || !targetCommunication.equals(
                device.negotiatedCommunicationBusiness())) {
            reasons.add("常驻通信代理不能与目标业务程序协商所需协议");
        }
        if (!targetUpdater.equals(device.updaterBusinessProtocol())
                || !targetUpdater.equals(device.negotiatedUpdaterBusiness())) {
            reasons.add("设备更新器不能与目标业务程序协商所需协议");
        }
        if (device.businessPackageFormatVersion() == null
                || device.businessPackageFormatVersion()
                != target.packageFormatVersion()) {
            reasons.add("设备更新器不支持目标业务发布包格式");
        }
        if (target.backendCommandContractVersion()
                != BACKEND_COMMAND_CONTRACT_VERSION
                || target.deviceEventContractVersion()
                != DEVICE_EVENT_CONTRACT_VERSION) {
            reasons.add("目标业务程序与当前后端业务消息格式不兼容");
        }
        boolean uartCompatible = "READY".equals(device.uartState())
                && target.uartFamily().equals(device.uartProtocolFamily());
        if (uartCompatible && "ECOBIN_UART".equals(target.uartFamily())) {
            uartCompatible = target.uartMajor() != null
                    && target.uartMinor() != null
                    && target.uartMajor().equals(device.uartProtocolMajor())
                    && target.uartMinor().equals(device.uartProtocolMinor());
        } else if (uartCompatible
                && "FIXED_FRAME".equals(target.uartFamily())) {
            uartCompatible = target.fixedFrameRevision() != null
                    && target.fixedFrameRevision().equals(
                    device.mcuFixedFrameRevision());
        } else {
            uartCompatible = false;
        }
        if (!uartCompatible) {
            reasons.add("目标业务程序与设备当前单片机串口协议不兼容");
        }
        if (!capabilitiesContain(
                device.capabilityBitmapHex(), target.requiredMcuCapabilities())) {
            reasons.add("设备当前单片机缺少目标业务程序要求的能力");
        }
        long activeBusinessPlans = excludedRolloutId == null
                ? jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_edge_software_deployment deployment
                        JOIN dev_edge_software_rollout rollout
                          ON rollout.id = deployment.rollout_id
                        WHERE deployment.asset_id = ?
                          AND deployment.deployment_status <> 'LOCAL_CANCELLED'
                          AND rollout.rollout_status NOT IN (
                              'COMPLETED', 'STOPPED', 'VALIDATION_FAILED'
                          )
                        """, Long.class, device.assetId())
                : jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_edge_software_deployment deployment
                        JOIN dev_edge_software_rollout rollout
                          ON rollout.id = deployment.rollout_id
                        WHERE deployment.asset_id = ?
                          AND deployment.deployment_status <> 'LOCAL_CANCELLED'
                          AND rollout.id <> ?
                          AND rollout.rollout_status NOT IN (
                              'COMPLETED', 'STOPPED', 'VALIDATION_FAILED'
                          )
                        """, Long.class, device.assetId(), excludedRolloutId);
        if (activeBusinessPlans > 0) {
            reasons.add("设备已经在另一项尚未停止的业务更新计划中");
        }
        long activeMcuUpdates = jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM dev_mcu_firmware_deployment deployment
                JOIN dev_mcu_firmware_rollout rollout
                  ON rollout.id = deployment.rollout_id
                WHERE deployment.asset_id = ?
                  AND rollout.rollout_status NOT IN ('COMPLETED', 'STOPPED')
                  AND deployment.deployment_status NOT IN (
                      'SUCCEEDED', 'ROLLED_BACK', 'FAILED_LOCKED', 'REJECTED', 'LOCAL_CANCELLED'
                  )
                """, Long.class, device.assetId());
        if (activeMcuUpdates > 0) {
            reasons.add("设备当前有尚未结束的单片机更新");
        }
        if (!reasons.isEmpty()) {
            throw ineligible(hardwareSn, String.join("；", reasons));
        }
        String baselineKind = imageBridge
                ? IMAGE_BRIDGE_BASELINE : BUSINESS_RELEASE_BASELINE;
        UUID activeReleaseUid = imageBridge
                ? null : UUID.fromString(device.activeReleaseUid());
        Map<String, Object> snapshot = new LinkedHashMap<>();
        snapshot.put("schemaVersion", 1);
        snapshot.put("hardwareSn", hardwareSn);
        snapshot.put("targetReleaseUid", target.uid().toString());
        snapshot.put("targetReleaseSequence", target.releaseSequence());
        snapshot.put("sourceSoftwareFactId", device.softwareFactId());
        snapshot.put("sourceManagementStateSequence", device.managementStateSequence());
        snapshot.put("currentBusinessBaselineKind", baselineKind);
        snapshot.put("currentBusinessReleaseUid",
                activeReleaseUid == null ? null : activeReleaseUid.toString());
        snapshot.put("currentBusinessReleaseSequence",
                device.activeReleaseSequence());
        snapshot.put("compatibility", "设备当前事实满足目标发布的安装前兼容检查");
        snapshot.put("onlineCheck", "下发阶段重新检查，临时离线不从计划中删除");
        snapshot.put("diskSpaceCheck", "由设备更新器在下载和停止业务前检查");
        byte[] canonical = canonicalizer.canonicalBytes(snapshot);
        return new EligibleDevice(
                device.assetId(),
                device.tenantId(),
                device.organizationId(),
                device.softwareFactId(),
                device.managementStateSequence(),
                baselineKind,
                activeReleaseUid,
                device.activeReleaseSequence(),
                new String(canonical, StandardCharsets.UTF_8),
                sha256(canonical));
    }

    private ReleaseRow requireRelease(UUID uid, boolean forUpdate) {
        if (forUpdate) {
            List<Long> locked = jdbc.query("""
                    SELECT id
                    FROM dev_edge_software_release_control
                    WHERE release_uid = ?
                    FOR UPDATE
                    """, (rs, ignored) -> rs.getLong("id"), uid.toString());
            if (locked.isEmpty()) {
                throw notFound("找不到该业务发布");
            }
            if (locked.size() != 1) {
                throw new IllegalStateException(
                        "business release UID is not unique");
            }
        }
        List<ReleaseRow> rows = jdbc.query(
                RELEASE_SELECT + " WHERE release_control.release_uid = ?",
                (rs, ignored) -> releaseRow(rs),
                uid.toString());
        if (rows.isEmpty()) {
            throw notFound("找不到该业务发布");
        }
        if (rows.size() != 1) {
            throw new IllegalStateException("business release UID is not unique");
        }
        return rows.getFirst();
    }

    private ReleaseRow releaseByCreateOperation(UUID operationUid, boolean forUpdate) {
        if (forUpdate) {
            List<Long> locked = jdbc.query("""
                    SELECT id
                    FROM dev_edge_software_release_control
                    WHERE create_operation_uid = ?
                    FOR UPDATE
                    """, (rs, ignored) -> rs.getLong("id"),
                    operationUid.toString());
            if (locked.isEmpty()) {
                return null;
            }
            if (locked.size() != 1) {
                throw new IllegalStateException(
                        "business release create operation is not unique");
            }
        }
        List<ReleaseRow> rows = jdbc.query(
                RELEASE_SELECT
                        + " WHERE release_control.create_operation_uid = ?",
                (rs, ignored) -> releaseRow(rs),
                operationUid.toString());
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private ReleaseRow releaseById(long id) {
        List<ReleaseRow> rows = jdbc.query(
                RELEASE_SELECT + " WHERE release_control.id = ?",
                (rs, ignored) -> releaseRow(rs),
                id);
        if (rows.size() != 1) {
            throw new IllegalStateException("business release row is missing");
        }
        return rows.getFirst();
    }

    private RolloutRow requireRollout(UUID uid, boolean forUpdate) {
        List<RolloutRow> rows = jdbc.query(
                ROLLOUT_SELECT + " WHERE rollout.rollout_uid = ?"
                        + (forUpdate ? " FOR UPDATE" : ""),
                (rs, ignored) -> rolloutRow(rs),
                uid.toString());
        if (rows.isEmpty()) {
            throw notFound("找不到该业务更新计划");
        }
        return rows.getFirst();
    }

    private RolloutRow rolloutByCreateOperation(
            UUID operationUid, boolean forUpdate) {
        List<RolloutRow> rows = jdbc.query(
                ROLLOUT_SELECT + " WHERE rollout.create_operation_uid = ?"
                        + (forUpdate ? " FOR UPDATE" : ""),
                (rs, ignored) -> rolloutRow(rs),
                operationUid.toString());
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private DeploymentRow requireValidationDeployment(
            long rolloutId,
            boolean forUpdate) {
        List<DeploymentRow> rows = jdbc.query("""
                SELECT deployment.id, deployment.deployment_uid,
                       deployment.rollout_id, deployment.release_id,
                       deployment.asset_id, asset.hardware_sn,
                       deployment.deployment_status,
                       deployment.command_uid,
                       deployment.reliable_task_uid,
                       deployment.edge_update_uid,
                       deployment.control_sequence,
                       deployment.cancel_command_uid,
                       deployment.cancel_reliable_task_uid,
                       deployment.cancel_control_sequence,
                       deployment.cancellation_status,
                       deployment.cancel_reason,
                       deployment.cancel_requested_by_platform_admin_id,
                       deployment.cancel_requested_at,
                       deployment.cancel_result_at,
                       deployment.stage_sequence,
                       deployment.download_attempt_count,
                       deployment.target_attempt_count,
                       deployment.rollback_attempt_count
                       , deployment.deployment_kind,
                       rollout.release_control_id,
                       rollout.rollout_status
                FROM dev_edge_software_deployment deployment
                JOIN dev_device_asset asset ON asset.id = deployment.asset_id
                JOIN dev_edge_software_rollout rollout
                  ON rollout.id = deployment.rollout_id
                WHERE deployment.rollout_id = ?
                  AND deployment.deployment_kind = 'VALIDATION'
                """ + (forUpdate ? " FOR UPDATE" : ""),
                (rs, ignored) -> new DeploymentRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("deployment_uid")),
                        rs.getLong("rollout_id"),
                        rs.getLong("release_id"),
                        rs.getLong("asset_id"),
                        rs.getString("hardware_sn"),
                        rs.getString("deployment_status"),
                        nullableUuid(rs, "command_uid"),
                        nullableUuid(rs, "reliable_task_uid"),
                        nullableUuid(rs, "edge_update_uid"),
                        nullableLong(rs, "control_sequence"),
                        nullableUuid(rs, "cancel_command_uid"),
                        nullableUuid(rs, "cancel_reliable_task_uid"),
                        nullableLong(rs, "cancel_control_sequence"),
                        rs.getString("cancellation_status"),
                        rs.getString("cancel_reason"),
                        nullableLong(rs, "cancel_requested_by_platform_admin_id"),
                        localDateTime(rs, "cancel_requested_at"),
                        localDateTime(rs, "cancel_result_at"),
                        rs.getLong("stage_sequence"),
                        rs.getInt("download_attempt_count"),
                        rs.getInt("target_attempt_count"),
                        rs.getInt("rollback_attempt_count"),
                        rs.getString("deployment_kind"),
                        rs.getLong("release_control_id"),
                        rs.getString("rollout_status")),
                rolloutId);
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "business rollout must contain one validation deployment");
        }
        return rows.getFirst();
    }

    private DeploymentRow requireDeployment(
            UUID deploymentUid,
            boolean forUpdate) {
        List<DeploymentRow> rows = jdbc.query("""
                SELECT deployment.id, deployment.deployment_uid,
                       deployment.rollout_id, deployment.release_id,
                       deployment.asset_id, asset.hardware_sn,
                       deployment.deployment_status,
                       deployment.command_uid,
                       deployment.reliable_task_uid,
                       deployment.edge_update_uid,
                       deployment.control_sequence,
                       deployment.cancel_command_uid,
                       deployment.cancel_reliable_task_uid,
                       deployment.cancel_control_sequence,
                       deployment.cancellation_status,
                       deployment.cancel_reason,
                       deployment.cancel_requested_by_platform_admin_id,
                       deployment.cancel_requested_at,
                       deployment.cancel_result_at,
                       deployment.stage_sequence,
                       deployment.download_attempt_count,
                       deployment.target_attempt_count,
                       deployment.rollback_attempt_count,
                       deployment.deployment_kind,
                       rollout.release_control_id,
                       rollout.rollout_status
                FROM dev_edge_software_deployment deployment
                JOIN dev_device_asset asset ON asset.id = deployment.asset_id
                JOIN dev_edge_software_rollout rollout
                  ON rollout.id = deployment.rollout_id
                WHERE deployment.deployment_uid = ?
                """ + (forUpdate ? " FOR UPDATE" : ""),
                (rs, ignored) -> new DeploymentRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("deployment_uid")),
                        rs.getLong("rollout_id"),
                        rs.getLong("release_id"),
                        rs.getLong("asset_id"),
                        rs.getString("hardware_sn"),
                        rs.getString("deployment_status"),
                        nullableUuid(rs, "command_uid"),
                        nullableUuid(rs, "reliable_task_uid"),
                        nullableUuid(rs, "edge_update_uid"),
                        nullableLong(rs, "control_sequence"),
                        nullableUuid(rs, "cancel_command_uid"),
                        nullableUuid(rs, "cancel_reliable_task_uid"),
                        nullableLong(rs, "cancel_control_sequence"),
                        rs.getString("cancellation_status"),
                        rs.getString("cancel_reason"),
                        nullableLong(rs, "cancel_requested_by_platform_admin_id"),
                        localDateTime(rs, "cancel_requested_at"),
                        localDateTime(rs, "cancel_result_at"),
                        rs.getLong("stage_sequence"),
                        rs.getInt("download_attempt_count"),
                        rs.getInt("target_attempt_count"),
                        rs.getInt("rollback_attempt_count"),
                        rs.getString("deployment_kind"),
                        rs.getLong("release_control_id"),
                        rs.getString("rollout_status")),
                deploymentUid.toString());
        if (rows.size() != 1) {
            throw new IllegalArgumentException(
                    "business runtime deployment is not authoritative");
        }
        return rows.getFirst();
    }

    private ReleaseActionRow releaseAction(UUID operationUid) {
        List<ReleaseActionRow> rows = jdbc.query("""
                SELECT release_control_id, action_type, reason
                FROM dev_edge_software_release_action
                WHERE operation_uid = ?
                """, (rs, ignored) -> new ReleaseActionRow(
                rs.getLong("release_control_id"),
                rs.getString("action_type"),
                rs.getString("reason")), operationUid.toString());
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private RolloutActionRow rolloutAction(UUID operationUid) {
        List<RolloutActionRow> rows = jdbc.query("""
                SELECT rollout_id, action_type, reason
                FROM dev_edge_software_rollout_action
                WHERE operation_uid = ?
                """, (rs, ignored) -> new RolloutActionRow(
                rs.getLong("rollout_id"),
                rs.getString("action_type"),
                rs.getString("reason")), operationUid.toString());
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private ReleaseView releaseView(ReleaseRow row, boolean includeActions) {
        CompatibilityDeclarationView compatibility = row.declarationId() == null
                ? null : new CompatibilityDeclarationView(
                row.packageFormatVersion(),
                row.backendCommandContractVersion(),
                row.deviceEventContractVersion(),
                row.communicationMajor() + "." + row.communicationMinor(),
                row.updaterMajor() + "." + row.updaterMinor(),
                row.uartFamily().equals("FIXED_FRAME")
                        ? "固定帧修订 " + row.fixedFrameRevision()
                        : "EcoBin UART " + row.uartMajor() + "." + row.uartMinor(),
                row.requiredMcuCapabilities(),
                row.providedBusinessCapabilities());
        List<ReleaseActionView> actions = includeActions
                ? releaseActions(row.id()) : List.of();
        return new ReleaseView(
                row.uid(),
                row.versionName(),
                row.releaseSequence(),
                row.status(),
                releaseStatusLabel(row.status()),
                releaseStatusDescription(row.status()),
                row.packageObjectKey(),
                row.signatureObjectKey(),
                row.packageSha256(),
                row.packageSize(),
                row.signatureSha256(),
                row.signingKeyId(),
                row.artifactUploaded(),
                row.verificationErrorMessage(),
                row.releaseNotes(),
                row.createdBy(),
                row.verifiedBy(),
                instant(row.verifiedAt()),
                row.approvedBy(),
                instant(row.approvedAt()),
                row.suspendedBy(),
                instant(row.suspendedAt()),
                row.suspensionReason(),
                row.retiredBy(),
                instant(row.retiredAt()),
                row.retirementReason(),
                instant(row.createdAt()),
                instant(row.updatedAt()),
                compatibility,
                actions);
    }

    private RolloutView rolloutView(RolloutRow row, boolean includeDetails) {
        List<DeploymentView> deployments = includeDetails
                ? deployments(row.id()) : List.of();
        List<RolloutActionView> actions = includeDetails
                ? rolloutActions(row.id()) : List.of();
        return new RolloutView(
                row.uid(),
                releaseView(releaseById(row.releaseControlId()), false),
                row.status(),
                rolloutStatusLabel(row.status()),
                row.batchSize(),
                row.maximumWaveNo(),
                row.validationHardwareSn(),
                row.observationSeconds() / 60,
                row.downloadSeconds() / 60,
                row.drainSeconds() / 60,
                row.maximumRetryCount(),
                row.remoteDispatchEnabled(),
                row.reason(),
                row.createdBy(),
                row.stoppedBy(),
                instant(row.stoppedAt()),
                row.stopReason(),
                instant(row.createdAt()),
                instant(row.updatedAt()),
                deployments,
                actions);
    }

    private List<ReleaseActionView> releaseActions(long releaseId) {
        return jdbc.query("""
                SELECT action.action_type, action.resulting_status,
                       actor.display_name, action.reason, action.created_at
                FROM dev_edge_software_release_action action
                JOIN iam_platform_admin actor
                  ON actor.id = action.requested_by_platform_admin_id
                WHERE action.release_control_id = ?
                ORDER BY action.id
                """, (rs, ignored) -> {
            String action = rs.getString("action_type");
            String status = rs.getString("resulting_status");
            return new ReleaseActionView(
                    action,
                    actionLabel(action),
                    status,
                    releaseStatusLabel(status),
                    rs.getString("display_name"),
                    rs.getString("reason"),
                    instant(localDateTime(rs, "created_at")));
        }, releaseId);
    }

    private List<DeploymentView> deployments(long rolloutId) {
        return jdbc.query("""
                SELECT deployment.deployment_uid, asset.hardware_sn,
                       tenant.tenant_code, organization.organization_code,
                       deployment.deployment_kind, deployment.wave_no,
                       deployment.deployment_status,
                       deployment.cancellation_status,
                       deployment.cancel_reason,
                       deployment.cancel_requested_at,
                       deployment.cancel_result_at,
                       deployment.business_admission_state,
                       deployment.download_attempt_count,
                       deployment.target_attempt_count,
                       deployment.rollback_attempt_count,
                       deployment.installed_version_name,
                       deployment.database_restored,
                       deployment.error_code,
                       deployment.source_management_state_sequence,
                       deployment.source_business_baseline_kind,
                       deployment.source_business_release_uid,
                       deployment.source_business_release_sequence,
                       deployment.created_at, deployment.queued_at,
                       deployment.completed_at, deployment.updated_at
                FROM dev_edge_software_deployment deployment
                JOIN dev_device_asset asset ON asset.id = deployment.asset_id
                LEFT JOIN iam_tenant tenant ON tenant.id = deployment.tenant_id
                LEFT JOIN iam_organization organization
                  ON organization.id = deployment.organization_id
                 AND organization.tenant_id = deployment.tenant_id
                WHERE deployment.rollout_id = ?
                ORDER BY deployment.wave_no, deployment.id
                """, (rs, ignored) -> {
            String kind = rs.getString("deployment_kind");
            return new DeploymentView(
                    UUID.fromString(rs.getString("deployment_uid")),
                    rs.getString("hardware_sn"),
                    rs.getString("tenant_code"),
                    rs.getString("organization_code"),
                    kind,
                    "VALIDATION".equals(kind) ? "验证设备" : "灰度批次设备",
                    rs.getInt("wave_no"),
                    rs.getString("deployment_status"),
                    deploymentStatusLabel(
                            rs.getString("deployment_status")),
                    rs.getString("cancellation_status"),
                    cancellationStatusLabel(
                            rs.getString("cancellation_status")),
                    rs.getString("cancel_reason"),
                    instant(localDateTime(rs, "cancel_requested_at")),
                    instant(localDateTime(rs, "cancel_result_at")),
                    businessAdmissionLabel(
                            rs.getString("business_admission_state")),
                    rs.getInt("download_attempt_count"),
                    rs.getInt("target_attempt_count"),
                    rs.getInt("rollback_attempt_count"),
                    rs.getString("installed_version_name"),
                    rs.getBoolean("database_restored"),
                    deploymentErrorMessage(rs.getString("error_code")),
                    IMAGE_BRIDGE_BASELINE.equals(rs.getString(
                            "source_business_baseline_kind"))
                            ? "创建计划时设备运行镜像内置业务程序，实际软件事实满足首次更新检查；下发前仍会重新检查"
                            : "创建计划时的实际软件事实满足目标发布；下发前仍会重新检查",
                    rs.getLong("source_management_state_sequence"),
                    nullableUuid(rs.getString("source_business_release_uid")),
                    nullableLong(rs, "source_business_release_sequence"),
                    instant(localDateTime(rs, "created_at")),
                    instant(localDateTime(rs, "queued_at")),
                    instant(localDateTime(rs, "completed_at")),
                    instant(localDateTime(rs, "updated_at")));
        }, rolloutId);
    }

    private List<RolloutActionView> rolloutActions(long rolloutId) {
        return jdbc.query("""
                SELECT action.action_type, action.resulting_status,
                       actor.display_name, action.reason, action.created_at
                FROM dev_edge_software_rollout_action action
                JOIN iam_platform_admin actor
                  ON actor.id = action.requested_by_platform_admin_id
                WHERE action.rollout_id = ?
                ORDER BY action.id
                """, (rs, ignored) -> {
            String action = rs.getString("action_type");
            String status = rs.getString("resulting_status");
            return new RolloutActionView(
                    action,
                    switch (action) {
                        case "CREATE" -> "创建计划";
                        case "START_VALIDATION" -> "开始验证设备更新";
                        case "REQUEST_CANCEL" -> "请求安全取消设备更新";
                        case "STOP" -> "停止计划";
                        default -> "无法识别的操作";
                    },
                    status,
                    rolloutStatusLabel(status),
                    rs.getString("display_name"),
                    rs.getString("reason"),
                    instant(localDateTime(rs, "created_at")));
        }, rolloutId);
    }

    private void requireRolloutReplay(
            RolloutRow replay,
            CreateRolloutRequest request,
            Set<String> expectedTargets,
            int batchSize,
            String reason) {
        Set<String> actualTargets = new LinkedHashSet<>();
        for (DeploymentView deployment : deployments(replay.id())) {
            actualTargets.add(deployment.hardwareSn());
        }
        if (!releaseById(replay.releaseControlId()).uid().equals(request.releaseUid())
                || !replay.validationHardwareSn().equals(
                hardwareSn(request.validationHardwareSn()))
                || replay.batchSize() != batchSize
                || !replay.reason().equals(reason)
                || !actualTargets.equals(expectedTargets)) {
            throw idempotencyConflict("同一幂等键不能创建另一项业务更新计划");
        }
    }

    private byte[] declarationSha256(VerifiedRelease release) {
        Map<String, Object> declaration = new LinkedHashMap<>();
        declaration.put("releaseUid", release.releaseUid().toString());
        declaration.put("versionName", release.versionName());
        declaration.put("releaseSequence", release.releaseSequence());
        declaration.put("packageSha256", release.packageSha256());
        declaration.put("packageFormatVersion", release.packageFormatVersion());
        declaration.put("backendCommandContractVersion",
                release.backendCommandContractVersion());
        declaration.put("deviceEventContractVersion",
                release.deviceEventContractVersion());
        declaration.put("communicationBusinessProtocolMajor",
                release.communicationBusinessProtocolMajor());
        declaration.put("communicationBusinessProtocolMinor",
                release.communicationBusinessProtocolMinor());
        declaration.put("updaterBusinessProtocolMajor",
                release.updaterBusinessProtocolMajor());
        declaration.put("updaterBusinessProtocolMinor",
                release.updaterBusinessProtocolMinor());
        declaration.put("uartProtocolFamily", release.uartProtocolFamily());
        declaration.put("uartProtocolMajor", release.uartProtocolMajor());
        declaration.put("uartProtocolMinor", release.uartProtocolMinor());
        declaration.put("requiredFixedFrameRevision",
                release.requiredFixedFrameRevision());
        declaration.put("requiredMcuCapabilityBitmapHex",
                release.requiredMcuCapabilityBitmapHex());
        declaration.put("providedBusinessCapabilityBitmapHex",
                release.providedBusinessCapabilityBitmapHex());
        return canonicalizer.payloadSha256(declaration);
    }

    private void insertReleaseAction(
            UUID operationUid,
            long releaseId,
            String action,
            long adminId,
            String reason,
            String resultingStatus,
            LocalDateTime now) {
        jdbc.update("""
                INSERT INTO dev_edge_software_release_action (
                    operation_uid, release_control_id, action_type,
                    requested_by_platform_admin_id, reason,
                    resulting_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, operationUid.toString(), releaseId, action,
                adminId, truncate(reason, 500), resultingStatus, now);
    }

    private void insertRolloutAction(
            UUID operationUid,
            long rolloutId,
            String action,
            long adminId,
            String reason,
            String resultingStatus,
            LocalDateTime now) {
        jdbc.update("""
                INSERT INTO dev_edge_software_rollout_action (
                    operation_uid, rollout_id, action_type,
                    requested_by_platform_admin_id, reason,
                    resulting_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, operationUid.toString(), rolloutId, action,
                adminId, reason, resultingStatus, now);
    }

    private static void requireActionReplay(
            ReleaseActionRow row,
            long releaseId,
            String action,
            String reason) {
        if (row.releaseId() != releaseId
                || !row.action().equals(action)
                || !row.reason().equals(reason)) {
            throw idempotencyConflict("同一幂等键已经用于另一项发布操作");
        }
    }

    private static void requireRolloutActionReplay(
            RolloutActionRow row,
            long rolloutId,
            String action,
            String reason) {
        if (row.rolloutId() != rolloutId
                || !row.action().equals(action)
                || !row.reason().equals(reason)) {
            throw idempotencyConflict("同一幂等键已经用于另一项灰度操作");
        }
    }

    private static void requireArtifactReplay(
            ReleaseRow release,
            String signingKeyId,
            FileIdentity packageIdentity,
            FileIdentity signatureIdentity) {
        if (!release.artifactUploaded()
                || !signingKeyId.equals(release.signingKeyId())
                || !packageIdentity.sha256().equals(release.packageSha256())
                || packageIdentity.size() != release.packageSize()
                || !signatureIdentity.sha256().equals(
                release.signatureSha256())) {
            throw idempotencyConflict(
                    "同一幂等键不能用于不同的业务发布制品");
        }
    }

    private AuthorizedDeviceScope authorizePlatform() {
        AuthorizedDeviceScope actor = authorization.authorize(
                new DeviceScopeAuthorizationQuery(
                        true, null, null, "device.manage"));
        if (!actor.platformActor()) {
            throw new TargetApiException(
                    403,
                    "IDENTITY.PLATFORM_REQUIRED",
                    "只有平台管理员可以管理香橙派业务发布");
        }
        return actor;
    }

    /**
     * Artifact upload and verification deliberately perform slow filesystem
     * and object-storage work outside one database transaction. The shared
     * authorization port nevertheless requires a transaction for its
     * identity lookup and its transaction-bound persistence reference, so
     * consume that reference inside the same short transaction and return
     * only the plain administrator identifier.
     */
    private long authorizePlatformAdminForExternalIo() {
        return Objects.requireNonNull(
                transactions.execute(status ->
                        platformAdminId(authorizePlatform())),
                "platform administrator identifier was not resolved");
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
                                "只有平台管理员可以管理香橙派业务发布");
                    }
                    result[0] = platformAdminId;
                });
        return result[0];
    }

    private void requireArtifactStorage() {
        BusinessReleaseArtifactStoragePort.Readiness readiness =
                artifacts.readiness();
        if (!readiness.available()) {
            throw new TargetApiException(
                    503,
                    "DEVICE.BUSINESS_RELEASE_STORAGE_UNAVAILABLE",
                    readiness.message());
        }
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static FileIdentity fileIdentity(
            Path path, long maximumSize, String name) {
        try {
            if (!Files.isRegularFile(path,
                    java.nio.file.LinkOption.NOFOLLOW_LINKS)) {
                throw invalid(name + "不是普通文件");
            }
            long size = Files.size(path);
            if (size <= 0 || size > maximumSize) {
                throw invalid(name + "大小超出允许范围");
            }
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (InputStream input = Files.newInputStream(path)) {
                byte[] buffer = new byte[1024 * 1024];
                int count;
                while ((count = input.read(buffer)) >= 0) {
                    if (count > 0) {
                        digest.update(buffer, 0, count);
                    }
                }
            }
            return new FileIdentity(
                    HexFormat.of().formatHex(digest.digest()), size);
        } catch (TargetApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw invalid(name + "不可读");
        }
    }

    private static void deleteVerificationDirectory(Path directory) {
        if (directory == null) {
            return;
        }
        try {
            Files.deleteIfExists(directory.resolve("package.tar.gz"));
            Files.deleteIfExists(directory.resolve("package.sig"));
            Files.deleteIfExists(directory);
        } catch (IOException ignored) {
            // Temporary cleanup does not change the persisted verification result.
        }
    }

    private static boolean capabilitiesContain(String actual, String required) {
        if (actual == null || required == null
                || !actual.matches("^[0-9a-f]{16}$")
                || !required.matches("^[0-9a-f]{16}$")) {
            return false;
        }
        long actualBits = Long.parseUnsignedLong(actual, 16);
        long requiredBits = Long.parseUnsignedLong(required, 16);
        return (requiredBits & ~actualBits) == 0;
    }

    private static UUID derivedOperationUid(UUID source, String suffix) {
        byte[] digest = sha256((source + ":" + suffix)
                .getBytes(StandardCharsets.UTF_8));
        digest[6] = (byte) ((digest[6] & 0x0f) | 0x40);
        digest[8] = (byte) ((digest[8] & 0x3f) | 0x80);
        ByteBuffer bytes = ByteBuffer.wrap(digest);
        return new UUID(bytes.getLong(), bytes.getLong());
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (Exception exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static String packageKey(UUID releaseUid) {
        return "edge-runtime/releases/" + releaseUid + "/package.tar.gz";
    }

    private static String signatureKey(UUID releaseUid) {
        return "edge-runtime/releases/" + releaseUid + "/package.sig";
    }

    private static String versionName(String value) {
        if (value == null
                || value.length() > 32
                || !value.matches(SEMANTIC_VERSION)) {
            throw invalid("业务版本名必须是最长 32 个字符的语义版本，例如 1.4.0");
        }
        return value;
    }

    private static String signingKeyId(String value) {
        if (value == null
                || !value.matches("^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")) {
            throw invalid("签名密钥编号格式不正确");
        }
        return value;
    }

    private static String hardwareSn(String value) {
        if (value == null || !value.matches("^[A-Za-z0-9_-]{8,64}$")) {
            throw invalid("设备硬件 SN 格式不正确");
        }
        return value;
    }

    private static String requiredReason(String value) {
        if (value == null || value.isBlank() || value.trim().length() > 500) {
            throw invalid("必须填写不超过 500 字的操作原因");
        }
        return value.trim();
    }

    private static String nullableTrimmed(
            String value, int maximum, String field) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String normalized = value.trim();
        if (normalized.length() > maximum) {
            throw invalid(field + "过长");
        }
        return normalized;
    }

    private static void requireUuidV4(UUID value, String field) {
        if (value == null || value.version() != 4 || value.variant() != 2) {
            throw invalid(field + " 必须是 UUIDv4");
        }
    }

    private static long requireGeneratedId(KeyHolder holder, String target) {
        Number key = holder.getKey();
        if (key == null) {
            throw new IllegalStateException(target + " generated key is missing");
        }
        return key.longValue();
    }

    private static void setNullableInteger(
            java.sql.PreparedStatement statement,
            int index,
            Integer value) throws java.sql.SQLException {
        if (value == null) {
            statement.setNull(index, java.sql.Types.INTEGER);
        } else {
            statement.setInt(index, value);
        }
    }

    private static int page(int value) {
        return value < 1 ? 1 : value;
    }

    private static int pageSize(int value) {
        return Math.max(1, Math.min(100, value));
    }

    private static String truncate(String value, int maximum) {
        return value.length() <= maximum ? value : value.substring(0, maximum);
    }

    private boolean businessProgressExists(
            UUID eventUid,
            long sourceInboxId) {
        Integer count = jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM dev_edge_software_deployment_progress
                WHERE event_uid = ? OR source_inbox_id = ?
                """, Integer.class, eventUid.toString(), sourceInboxId);
        return count != null && count > 0;
    }

    private boolean businessCancelResultExists(
            UUID eventUid,
            long sourceInboxId) {
        Integer count = jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM dev_edge_software_deployment_cancel_result
                WHERE event_uid = ? OR source_inbox_id = ?
                """, Integer.class, eventUid.toString(), sourceInboxId);
        return count != null && count > 0;
    }

    private SourceBaseline sourceBaseline(long deploymentId) {
        List<SourceBaseline> rows = jdbc.query("""
                SELECT deployment.source_business_baseline_kind,
                       fact.active_business_release_uid,
                       fact.active_business_version_name,
                       fact.active_business_release_sequence,
                       fact.active_business_package_sha256
                FROM dev_edge_software_deployment deployment
                JOIN dev_device_software_fact fact
                  ON fact.id = deployment.source_software_fact_id
                WHERE deployment.id = ?
                """, (rs, ignored) -> {
            String kind = rs.getString("source_business_baseline_kind");
            String releaseUid = rs.getString("active_business_release_uid");
            String versionName = rs.getString("active_business_version_name");
            Long releaseSequence = nullableLong(
                    rs, "active_business_release_sequence");
            byte[] packageSha256 = rs.getBytes(
                    "active_business_package_sha256");
            if (IMAGE_BRIDGE_BASELINE.equals(kind)
                    && releaseUid == null
                    && versionName == null
                    && releaseSequence == null
                    && packageSha256 == null) {
                return new SourceBaseline(kind, null);
            }
            if (!BUSINESS_RELEASE_BASELINE.equals(kind)
                    || releaseUid == null
                    || versionName == null
                    || versionName.isBlank()
                    || releaseSequence == null
                    || packageSha256 == null
                    || packageSha256.length != 32) {
                throw new IllegalStateException(
                        "frozen source business package identity is unavailable");
            }
            return new SourceBaseline(
                    kind,
                    new InstalledBusiness(
                            UUID.fromString(releaseUid),
                            versionName,
                            releaseSequence,
                            HexFormat.of().formatHex(packageSha256)));
        }, deploymentId);
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "frozen source business identity is unavailable");
        }
        return rows.getFirst();
    }

    private static JsonNode jsonObject(JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(field + " must be an object");
        }
        return value;
    }

    private static String jsonText(
            JsonNode parent,
            String field,
            int maximum) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null
                || !value.isTextual()
                || value.asText().isBlank()
                || value.asText().length() > maximum) {
            throw new IllegalArgumentException(field + " is invalid");
        }
        return value.asText();
    }

    private static void requireJsonText(
            JsonNode parent,
            String field,
            String expected) {
        if (!expected.equals(jsonText(parent, field, 64))) {
            throw new IllegalArgumentException(field + " differs");
        }
    }

    private static String jsonPattern(
            JsonNode parent,
            String field,
            String pattern,
            int maximum) {
        String value = jsonText(parent, field, maximum);
        if (!value.matches(pattern)) {
            throw new IllegalArgumentException(field + " has invalid format");
        }
        return value;
    }

    private static String nullableJsonPattern(
            JsonNode parent,
            String field,
            String pattern,
            int maximum) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        return jsonPattern(parent, field, pattern, maximum);
    }

    private static UUID jsonUuid(JsonNode parent, String field) {
        return UUID.fromString(jsonPattern(parent, field, UUID_V4, 36));
    }

    private static UUID nullableUuid(String value) {
        return value == null ? null : UUID.fromString(value);
    }

    private static UUID nullableJsonUuid(JsonNode parent, String field) {
        String value = nullableJsonPattern(parent, field, UUID_V4, 36);
        return value == null ? null : UUID.fromString(value);
    }

    private static long jsonLong(
            JsonNode parent,
            String field,
            long minimum,
            long maximum) {
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

    private static boolean jsonBoolean(JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isBoolean()) {
            throw new IllegalArgumentException(field + " must be boolean");
        }
        return value.asBoolean();
    }

    private static LocalDateTime nullableJsonInstant(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        try {
            return Instant.parse(jsonText(parent, field, 30))
                    .atOffset(ZoneOffset.UTC)
                    .toLocalDateTime();
        } catch (RuntimeException exception) {
            throw new IllegalArgumentException(field + " is not an instant");
        }
    }

    private static boolean isBusinessProgressStage(String stage) {
        return Set.of(
                "RECEIVED", "DOWNLOADING", "VERIFYING_PACKAGE",
                "PACKAGE_READY", "WAITING_FOR_IDLE", "MIGRATING_DATA",
                "ACTIVATING", "VERIFYING_TARGET", "OBSERVING",
                "ROLLING_BACK", "VERIFYING_ROLLBACK", "SUCCEEDED",
                "ROLLED_BACK", "DEFERRED", "REJECTED", "FAILED_LOCKED",
                "DOWNLOAD_AUTHORIZATION_REQUIRED", "CANCELLED")
                .contains(stage);
    }

    private static boolean isBusinessCancellationObservedStage(String stage) {
        return Set.of(
                "RECEIVED", "VERIFYING_PACKAGE", "PACKAGE_READY",
                "WAITING_FOR_IDLE", "MIGRATING_DATA", "ACTIVATING",
                "VERIFYING_TARGET", "OBSERVING", "ROLLING_BACK",
                "VERIFYING_ROLLBACK", "SUCCEEDED", "ROLLED_BACK",
                "DEFERRED", "REJECTED", "FAILED_LOCKED").contains(stage);
    }

    private static InstalledBusiness installedBusiness(JsonNode payload) {
        JsonNode releaseUid = payload.get("installedReleaseUid");
        JsonNode versionName = payload.get("installedVersionName");
        JsonNode releaseSequence = payload.get("installedReleaseSequence");
        JsonNode packageSha256 = payload.get("installedPackageSha256");
        boolean absent = (releaseUid == null || releaseUid.isNull())
                && (versionName == null || versionName.isNull())
                && (releaseSequence == null || releaseSequence.isNull())
                && (packageSha256 == null || packageSha256.isNull());
        if (absent) {
            return null;
        }
        if (releaseUid == null || releaseUid.isNull()
                || versionName == null || versionName.isNull()
                || releaseSequence == null || releaseSequence.isNull()
                || packageSha256 == null || packageSha256.isNull()) {
            throw new IllegalArgumentException(
                    "installed business identity must be all present or all null");
        }
        return new InstalledBusiness(
                jsonUuid(payload, "installedReleaseUid"),
                jsonText(payload, "installedVersionName", 32),
                jsonLong(payload, "installedReleaseSequence", 1,
                        9_007_199_254_740_991L),
                jsonPattern(
                        payload, "installedPackageSha256", SHA256, 64));
    }

    private static String timestamp(LocalDateTime value) {
        return value.toInstant(ZoneOffset.UTC).toString();
    }

    private static String releaseStatusLabel(String status) {
        return switch (status) {
            case "DRAFT" -> "草稿";
            case "VERIFYING" -> "正在校验发布包";
            case "VERIFICATION_FAILED" -> "校验未通过";
            case "AWAITING_APPROVAL" -> "等待批准";
            case "READY" -> "已批准，可创建计划";
            case "SUSPENDED" -> "已暂停";
            case "RETIRED" -> "已归档";
            default -> "状态无法识别";
        };
    }

    private static String releaseStatusDescription(String status) {
        return switch (status) {
            case "DRAFT" -> "等待上传离线签名的业务发布包和签名文件";
            case "VERIFYING" -> "后台正在读回制品并核对摘要、签名、文件清单和兼容声明";
            case "VERIFICATION_FAILED" -> "制品没有通过校验，不能批准或创建更新计划";
            case "AWAITING_APPROVAL" -> "制品校验已通过，仍需平台管理员单独批准";
            case "READY" -> "可以选择验证设备并创建更新计划；是否允许真实下发由全局开关和计划快照共同决定";
            case "SUSPENDED" -> "暂停创建新的更新计划，已有审计和制品继续保留";
            case "RETIRED" -> "不再允许创建新计划，历史和回滚引用继续保留";
            default -> "系统无法理解该状态，已停止后续操作";
        };
    }

    private static String rolloutStatusLabel(String status) {
        return switch (status) {
            case "DRAFT" -> "计划已建立，尚未下发";
            case "VALIDATING" -> "验证设备正在更新";
            case "AWAITING_PROMOTION" -> "验证成功，等待人工放行";
            case "VALIDATION_FAILED" -> "验证设备更新未通过";
            case "ACTIVE" -> "灰度发布进行中";
            case "COMPLETED" -> "更新计划已完成";
            case "STOPPED" -> "计划已停止";
            default -> "状态无法识别";
        };
    }

    private static String deploymentStatusLabel(String status) {
        return switch (status) {
            case "PLANNED" -> "已规划，尚未下发";
            case "QUEUED" -> "更新命令等待发送";
            case "RECEIVED" -> "设备已接收更新任务";
            case "DOWNLOADING" -> "设备正在下载业务程序";
            case "VERIFYING_PACKAGE" -> "设备正在校验发布包";
            case "PACKAGE_READY" -> "发布包已准备完成";
            case "WAITING_FOR_IDLE" -> "等待当前投递或清运结束";
            case "MIGRATING_DATA" -> "正在备份并迁移本地数据";
            case "ACTIVATING" -> "正在切换业务程序";
            case "VERIFYING_TARGET" -> "正在检查新业务程序";
            case "OBSERVING" -> "新业务程序观察中";
            case "ROLLING_BACK" -> "正在恢复上一版本";
            case "VERIFYING_ROLLBACK" -> "正在确认恢复结果";
            case "SUCCEEDED" -> "业务程序更新成功";
            case "ROLLED_BACK" -> "更新失败，已恢复上一版本";
            case "DEFERRED" -> "本次更新已延后";
            case "REJECTED" -> "设备拒绝本次更新";
            case "FAILED_LOCKED" -> "更新和恢复均失败，设备已安全锁定";
            case "DOWNLOAD_AUTHORIZATION_REQUIRED" -> "等待新的下载授权";
            case "LOCAL_CANCELLED" -> "设备禁用或报废，下发已取消";
            case "CANCELLED" -> "设备已安全取消本次更新";
            default -> "状态无法识别";
        };
    }

    private static String cancellationStatusLabel(String status) {
        return switch (status) {
            case "NONE" -> "尚未请求取消";
            case "QUEUED" -> "已请求取消，等待设备确认";
            case "CANCELLED" -> "设备已安全取消";
            case "TOO_LATE" -> "设备已开始切换，无法取消";
            default -> "取消状态无法识别";
        };
    }

    private static String businessAdmissionLabel(String state) {
        return switch (state) {
            case "OPEN" -> "可以接收新的投递和清运";
            case "DRAINING" -> "已停止接收新业务，正在等待当前业务结束";
            case "MAINTENANCE" -> "业务程序正在更新维护";
            case "LOCKED" -> "新业务已暂停，等待设备状态恢复";
            default -> "无法确认设备是否能接收新业务";
        };
    }

    private static String deploymentErrorMessage(String errorCode) {
        if (errorCode == null || errorCode.isBlank()) {
            return null;
        }
        return switch (errorCode) {
            case "DOWNLOAD_AUTHORIZATION_EXPIRED",
                    "DOWNLOAD_AUTHORIZATION_LOST",
                    "DOWNLOAD_AUTHORIZATION_SUPERSEDED" ->
                    "私有下载链接已经失效，后台正在为同一更新任务重新签发短期链接。";
            case "BUSINESS_DOWNLOAD_TEMPORARY_FAILURE",
                    "BUSINESS_DOWNLOAD_TIMEOUT",
                    "BUSINESS_DOWNLOAD_RETRY_EXHAUSTED" ->
                    "设备未能在允许次数内下载完业务程序包，请检查设备网络后重试。";
            case "BUSINESS_PACKAGE_OBJECT_NOT_FOUND" ->
                    "私有存储中找不到这份业务程序包，请核对发布制品。";
            case "BUSINESS_PACKAGE_SHA256_MISMATCH",
                    "BUSINESS_PACKAGE_SIZE_MISMATCH",
                    "BUSINESS_SIGNATURE_FILE_CONFLICT",
                    "BUSINESS_PACKAGE_INVALID",
                    "BUSINESS_ENVIRONMENT_UNSAFE",
                    "BUSINESS_PACKAGE_PATH_UNSAFE" ->
                    "设备校验业务程序包时发现内容、签名或文件结构不符合发布记录。";
            case "DEVICE_STORAGE_INSUFFICIENT" ->
                    "设备空间不足，无法同时保留当前版本、候选版本和回滚快照。";
            case "DEVICE_STORAGE_UNKNOWN" ->
                    "设备暂时无法确认剩余空间，本次更新没有开始切换程序。";
            case "BUSINESS_DRAIN_TIMEOUT",
                    "BUSINESS_ROLLBACK_DRAIN_TIMEOUT" ->
                    "设备等待当前投递或清运结束超时，本次更新没有强行中断现场业务。";
            case "BUSINESS_UPDATE_CANCEL_TOO_LATE" ->
                    "设备已经开始备份数据或切换程序，无法再安全取消；本次更新会继续完成或自动恢复上一版本。";
            case "BUSINESS_RELEASE_SEQUENCE_NOT_NEWER",
                    "BUSINESS_TARGET_ALREADY_INSTALLED" ->
                    "目标版本没有晚于设备当前版本，因此设备拒绝重复或降级安装。";
            case "BUSINESS_RELEASE_BASELINE_CHANGED",
                    "BUSINESS_ROLLBACK_BASELINE_UNAVAILABLE" ->
                    "设备当前版本与计划创建时不同，无法安全建立回滚基线。";
            case "BUSINESS_RUNTIME_NOT_READY",
                    "BUSINESS_RUNTIME_VERSION_MISMATCH",
                    "BUSINESS_TARGET_IDENTITY_MISMATCH" ->
                    "新业务程序没有按发布身份正常启动，设备已进入恢复流程。";
            case "BUSINESS_ROLLBACK_IDENTITY_MISMATCH",
                    "BUSINESS_MAINTENANCE_RECOVERY_FAILED" ->
                    "设备无法确认已经恢复到原业务版本，已暂停新的投递和清运。";
            case "HELPER_AUTHORIZATION_UNAVAILABLE",
                    "HELPER_BUSY",
                    "PRIVILEGED_ACTION_RECEIPT_INVALID",
                    "PRIVILEGED_ACTION_RESULT_UNKNOWN" ->
                    "设备本地更新执行器没有给出可信结果，已停止继续修改程序。";
            default -> "设备报告本次更新未成功，请根据页面请求编号查看运维诊断。";
        };
    }

    private static String actionLabel(String action) {
        return switch (action) {
            case "CREATE" -> "创建草稿";
            case "UPLOAD" -> "上传签名制品";
            case "START_VERIFICATION" -> "开始校验";
            case "VERIFICATION_PASSED" -> "校验通过";
            case "VERIFICATION_FAILED" -> "校验未通过";
            case "APPROVE" -> "批准发布";
            case "SUSPEND" -> "暂停发布";
            case "RESUME" -> "恢复发布";
            case "RETIRE" -> "归档发布";
            default -> "无法识别的操作";
        };
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(
                400, "DEVICE.BUSINESS_RELEASE_REQUEST_INVALID", message);
    }

    private static TargetApiException notFound(String message) {
        return new TargetApiException(
                404, "DEVICE.BUSINESS_RELEASE_NOT_FOUND", message);
    }

    private static TargetApiException conflict(String code, String message) {
        return new TargetApiException(409, code, message);
    }

    private static TargetApiException invalidTransition(String message) {
        return conflict("DEVICE.BUSINESS_RELEASE_STATE_CONFLICT", message);
    }

    private static TargetApiException idempotencyConflict(String message) {
        return conflict("DEVICE.IDEMPOTENCY_CONFLICT", message);
    }

    private static TargetApiException ineligible(String hardwareSn, String reason) {
        return conflict(
                "DEVICE.BUSINESS_RELEASE_DEVICE_INELIGIBLE",
                "设备 " + hardwareSn + " 当前不能加入更新计划：" + reason);
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static LocalDateTime localDateTime(
            java.sql.ResultSet result, String column)
            throws java.sql.SQLException {
        java.sql.Timestamp value = result.getTimestamp(column);
        return value == null ? null : value.toLocalDateTime();
    }

    private static Long nullableLong(
            java.sql.ResultSet result, String column)
            throws java.sql.SQLException {
        long value = result.getLong(column);
        return result.wasNull() ? null : value;
    }

    private static Integer nullableInteger(
            java.sql.ResultSet result, String column)
            throws java.sql.SQLException {
        int value = result.getInt(column);
        return result.wasNull() ? null : value;
    }

    private static Boolean nullableBoolean(
            java.sql.ResultSet result, String column)
            throws java.sql.SQLException {
        boolean value = result.getBoolean(column);
        return result.wasNull() ? null : value;
    }

    private static UUID nullableUuid(
            java.sql.ResultSet result, String column)
            throws java.sql.SQLException {
        String value = result.getString(column);
        return value == null ? null : UUID.fromString(value);
    }

    private static ReleaseRow releaseRow(java.sql.ResultSet rs)
            throws java.sql.SQLException {
        Long declarationId = nullableLong(rs, "declaration_id");
        return new ReleaseRow(
                rs.getLong("release_control_id"),
                UUID.fromString(rs.getString("release_uid")),
                rs.getString("version_name"),
                rs.getLong("release_sequence"),
                rs.getString("release_status"),
                nullableUuid(rs, "verification_operation_uid"),
                rs.getString("package_object_key"),
                rs.getString("signature_object_key"),
                hex(rs.getBytes("control_package_sha256")),
                nullableLong(rs, "package_size"),
                hex(rs.getBytes("signature_sha256")),
                rs.getBytes("signature_bytes"),
                rs.getString("signing_key_id"),
                rs.getString("verification_error_message"),
                rs.getString("release_notes"),
                rs.getString("created_by"),
                rs.getString("verified_by"),
                localDateTime(rs, "verified_at"),
                rs.getString("approved_by"),
                localDateTime(rs, "approved_at"),
                rs.getString("suspended_by"),
                localDateTime(rs, "suspended_at"),
                rs.getString("suspension_reason"),
                rs.getString("retired_by"),
                localDateTime(rs, "retired_at"),
                rs.getString("retirement_reason"),
                localDateTime(rs, "created_at"),
                localDateTime(rs, "updated_at"),
                declarationId,
                declarationId == null ? 0 : rs.getInt("package_format_version"),
                declarationId == null ? 0
                        : rs.getInt("backend_command_contract_version"),
                declarationId == null ? 0
                        : rs.getInt("device_event_contract_version"),
                declarationId == null ? 0
                        : rs.getInt("communication_business_protocol_major"),
                declarationId == null ? 0
                        : rs.getInt("communication_business_protocol_minor"),
                declarationId == null ? 0
                        : rs.getInt("updater_business_protocol_major"),
                declarationId == null ? 0
                        : rs.getInt("updater_business_protocol_minor"),
                rs.getString("uart_protocol_family"),
                nullableInteger(rs, "uart_protocol_major"),
                nullableInteger(rs, "uart_protocol_minor"),
                nullableInteger(rs, "required_fixed_frame_revision"),
                rs.getString("required_mcu_capability_bitmap_hex"),
                rs.getString("provided_business_capability_bitmap_hex"));
    }

    private static RolloutRow rolloutRow(java.sql.ResultSet rs)
            throws java.sql.SQLException {
        return new RolloutRow(
                rs.getLong("rollout_id"),
                UUID.fromString(rs.getString("rollout_uid")),
                rs.getLong("release_control_id"),
                rs.getString("rollout_status"),
                rs.getString("validation_hardware_sn"),
                rs.getInt("batch_size"),
                rs.getInt("maximum_wave_no"),
                rs.getInt("observation_window_seconds"),
                rs.getInt("download_timeout_seconds"),
                rs.getInt("drain_timeout_seconds"),
                rs.getInt("maximum_retry_count"),
                rs.getBoolean("remote_dispatch_enabled_snapshot"),
                rs.getString("change_reason"),
                rs.getString("created_by"),
                rs.getString("stopped_by"),
                localDateTime(rs, "stopped_at"),
                rs.getString("stop_reason"),
                localDateTime(rs, "created_at"),
                localDateTime(rs, "updated_at"));
    }

    private static String hex(byte[] value) {
        return value == null ? null : HexFormat.of().formatHex(value);
    }

    private static final String RELEASE_SELECT = """
            SELECT release_control.id AS release_control_id,
                   release_control.release_uid,
                   release_control.version_name,
                   release_control.release_sequence,
                   release_control.release_status,
                   release_control.verification_operation_uid,
                   release_control.package_object_key,
                   release_control.signature_object_key,
                   release_control.package_sha256 AS control_package_sha256,
                   release_control.package_size,
                   release_control.signature_sha256,
                   release_control.signature_bytes,
                   release_control.signing_key_id,
                   release_control.declaration_id,
                   release_control.verification_error_message,
                   release_control.release_notes,
                   creator.display_name AS created_by,
                   verifier.display_name AS verified_by,
                   release_control.verified_at,
                   approver.display_name AS approved_by,
                   release_control.approved_at,
                   suspender.display_name AS suspended_by,
                   release_control.suspended_at,
                   release_control.suspension_reason,
                   retirer.display_name AS retired_by,
                   release_control.retired_at,
                   release_control.retirement_reason,
                   release_control.created_at,
                   release_control.updated_at,
                   declaration.package_format_version,
                   declaration.backend_command_contract_version,
                   declaration.device_event_contract_version,
                   declaration.communication_business_protocol_major,
                   declaration.communication_business_protocol_minor,
                   declaration.updater_business_protocol_major,
                   declaration.updater_business_protocol_minor,
                   declaration.uart_protocol_family,
                   declaration.uart_protocol_major,
                   declaration.uart_protocol_minor,
                   declaration.required_fixed_frame_revision,
                   declaration.required_mcu_capability_bitmap_hex,
                   declaration.provided_business_capability_bitmap_hex
            FROM dev_edge_software_release_control release_control
            JOIN iam_platform_admin creator
              ON creator.id = release_control.created_by_platform_admin_id
            LEFT JOIN iam_platform_admin verifier
              ON verifier.id = release_control.verified_by_platform_admin_id
            LEFT JOIN iam_platform_admin approver
              ON approver.id = release_control.approved_by_platform_admin_id
            LEFT JOIN iam_platform_admin suspender
              ON suspender.id = release_control.suspended_by_platform_admin_id
            LEFT JOIN iam_platform_admin retirer
              ON retirer.id = release_control.retired_by_platform_admin_id
            LEFT JOIN dev_edge_software_release declaration
              ON declaration.id = release_control.declaration_id
            """;

    private static final String ROLLOUT_SELECT = """
            SELECT rollout.id AS rollout_id, rollout.rollout_uid,
                   rollout.release_control_id, rollout.rollout_status,
                   validation.hardware_sn AS validation_hardware_sn,
                   rollout.batch_size, rollout.maximum_wave_no,
                   rollout.observation_window_seconds,
                   rollout.download_timeout_seconds,
                   rollout.drain_timeout_seconds,
                   rollout.maximum_retry_count,
                   rollout.remote_dispatch_enabled_snapshot,
                   rollout.change_reason,
                   creator.display_name AS created_by,
                   stopper.display_name AS stopped_by,
                   rollout.stopped_at, rollout.stop_reason,
                   rollout.created_at, rollout.updated_at
            FROM dev_edge_software_rollout rollout
            JOIN dev_device_asset validation
              ON validation.id = rollout.validation_asset_id
            JOIN iam_platform_admin creator
              ON creator.id = rollout.created_by_platform_admin_id
            LEFT JOIN iam_platform_admin stopper
              ON stopper.id = rollout.stopped_by_platform_admin_id
            """;

    private static final String DEVICE_ELIGIBILITY_SELECT = """
            SELECT asset.id AS asset_id, asset.hardware_sn,
                   asset.tenant_id, asset.organization_id,
                   asset.lifecycle_status, asset.acceptance_status,
                   profile.architecture_generation,
                   projection.compatibility_status,
                   projection.business_admission_status,
                   fact.id AS software_fact_id,
                   fact.management_state_sequence,
                   fact.business_gate_state,
                   CONCAT(fact.communication_business_protocol_major, '.',
                          fact.communication_business_protocol_minor)
                       AS communication_business_protocol,
                   CONCAT(fact.updater_business_protocol_major, '.',
                          fact.updater_business_protocol_minor)
                       AS updater_business_protocol,
                   fact.business_package_format_version,
                   fact.active_business_release_uid,
                   fact.active_business_release_sequence,
                   fact.business_process_state,
                   fact.business_process_ready,
                   CONCAT(fact.negotiated_communication_business_major, '.',
                          fact.negotiated_communication_business_minor)
                       AS negotiated_communication_business,
                   CONCAT(fact.negotiated_updater_business_major, '.',
                          fact.negotiated_updater_business_minor)
                       AS negotiated_updater_business,
                   fact.mcu_fixed_frame_revision,
                   fact.uart_state,
                   fact.uart_protocol_family,
                   fact.uart_protocol_major,
                   fact.uart_protocol_minor,
                   fact.capability_bitmap_hex
            FROM dev_device_asset asset
            JOIN dev_device_management_profile profile
              ON profile.asset_id = asset.id
            JOIN dev_device_compatibility_projection projection
              ON projection.asset_id = asset.id
            LEFT JOIN dev_device_software_fact fact
              ON fact.id = projection.latest_software_fact_id
            WHERE asset.hardware_sn = ?
            """;

    private record FileIdentity(String sha256, long size) {
    }

    private record InstalledBusiness(
            UUID releaseUid,
            String versionName,
            long releaseSequence,
            String packageSha256) {
    }

    private record SourceBaseline(
            String kind,
            InstalledBusiness release) {
    }

    private record ReleaseActionRow(
            long releaseId, String action, String reason) {
    }

    private record RolloutActionRow(
            long rolloutId, String action, String reason) {
    }

    private record ReleaseRow(
            long id,
            UUID uid,
            String versionName,
            long releaseSequence,
            String status,
            UUID verificationOperationUid,
            String packageObjectKey,
            String signatureObjectKey,
            String packageSha256,
            Long packageSize,
            String signatureSha256,
            byte[] signatureBytes,
            String signingKeyId,
            String verificationErrorMessage,
            String releaseNotes,
            String createdBy,
            String verifiedBy,
            LocalDateTime verifiedAt,
            String approvedBy,
            LocalDateTime approvedAt,
            String suspendedBy,
            LocalDateTime suspendedAt,
            String suspensionReason,
            String retiredBy,
            LocalDateTime retiredAt,
            String retirementReason,
            LocalDateTime createdAt,
            LocalDateTime updatedAt,
            Long declarationId,
            int packageFormatVersion,
            int backendCommandContractVersion,
            int deviceEventContractVersion,
            int communicationMajor,
            int communicationMinor,
            int updaterMajor,
            int updaterMinor,
            String uartFamily,
            Integer uartMajor,
            Integer uartMinor,
            Integer fixedFrameRevision,
            String requiredMcuCapabilities,
            String providedBusinessCapabilities) {

        private ReleaseRow {
            signatureBytes = signatureBytes == null
                    ? null : signatureBytes.clone();
        }

        @Override
        public byte[] signatureBytes() {
            return signatureBytes == null ? null : signatureBytes.clone();
        }

        boolean artifactUploaded() {
            return packageSha256 != null;
        }
    }

    private record RolloutRow(
            long id,
            UUID uid,
            long releaseControlId,
            String status,
            String validationHardwareSn,
            int batchSize,
            int maximumWaveNo,
            int observationSeconds,
            int downloadSeconds,
            int drainSeconds,
            int maximumRetryCount,
            boolean remoteDispatchEnabled,
            String reason,
            String createdBy,
            String stoppedBy,
            LocalDateTime stoppedAt,
            String stopReason,
            LocalDateTime createdAt,
            LocalDateTime updatedAt) {
    }

    private record DeploymentRow(
            long id,
            UUID uid,
            long rolloutId,
            long releaseId,
            long assetId,
            String hardwareSn,
            String status,
            UUID commandUid,
            UUID taskUid,
            UUID edgeUpdateUid,
            Long controlSequence,
            UUID cancelCommandUid,
            UUID cancelTaskUid,
            Long cancelControlSequence,
            String cancellationStatus,
            String cancelReason,
            Long cancelRequestedByPlatformAdminId,
            LocalDateTime cancelRequestedAt,
            LocalDateTime cancelResultAt,
            long stageSequence,
            int downloadAttemptCount,
            int targetAttemptCount,
            int rollbackAttemptCount,
            String kind,
            long releaseControlId,
            String rolloutStatus) {
    }

    private record DeviceCandidate(
            long assetId,
            String hardwareSn,
            Long tenantId,
            Long organizationId,
            String lifecycleStatus,
            String acceptanceStatus,
            String architectureGeneration,
            String compatibilityStatus,
            String businessAdmissionStatus,
            Long softwareFactId,
            Long managementStateSequence,
            String businessGateState,
            String communicationBusinessProtocol,
            String updaterBusinessProtocol,
            Integer businessPackageFormatVersion,
            String activeReleaseUid,
            Long activeReleaseSequence,
            String businessProcessState,
            Boolean businessProcessReady,
            String negotiatedCommunicationBusiness,
            String negotiatedUpdaterBusiness,
            Integer mcuFixedFrameRevision,
            String uartState,
            String uartProtocolFamily,
            Integer uartProtocolMajor,
            Integer uartProtocolMinor,
            String capabilityBitmapHex) {
    }

    private record EligibleDevice(
            long assetId,
            Long tenantId,
            Long organizationId,
            long softwareFactId,
            long managementStateSequence,
            String baselineKind,
            UUID activeReleaseUid,
            Long activeReleaseSequence,
            String snapshotJson,
            byte[] snapshotSha256) {

        private EligibleDevice {
            snapshotSha256 = Arrays.copyOf(
                    snapshotSha256, snapshotSha256.length);
        }

        @Override
        public byte[] snapshotSha256() {
            return Arrays.copyOf(snapshotSha256, snapshotSha256.length);
        }
    }
}
