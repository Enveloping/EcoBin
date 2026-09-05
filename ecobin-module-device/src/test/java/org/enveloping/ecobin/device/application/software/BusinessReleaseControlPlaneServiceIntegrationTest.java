package org.enveloping.ecobin.device.application.software;

import org.enveloping.ecobin.device.api.port.BusinessReleaseArtifactStoragePort;
import org.enveloping.ecobin.device.api.port.BusinessReleaseSigningKeyPort;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedPlatformDeviceAssetFactEvent;
import org.enveloping.ecobin.device.application.software.BusinessReleasePackageVerifier.VerifiedRelease;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.CreateReleaseDraftRequest;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.CreateRolloutRequest;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.ReleaseView;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRef;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWakePort;
import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRef;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.DeviceScopePersistenceRef;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeviceScope;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import javax.sql.DataSource;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Arrays;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class BusinessReleaseControlPlaneServiceIntegrationTest {

    private static final String VALIDATION_SN = "SN-BUSINESS-VALIDATION-01";
    private static final String WAVE_SN = "SN-BUSINESS-WAVE-000001";
    private static final UUID CURRENT_RELEASE_UID = UUID.fromString(
            "10000000-0000-4000-8000-000000000001");
    private static final String CURRENT_PACKAGE_SHA256 = "c".repeat(64);

    @TempDir
    Path temporary;

    private JdbcTemplate jdbc;
    private BusinessReleaseControlPlaneService service;
    private BusinessReleasePackageVerifier verifier;

    @BeforeEach
    void setUp() {
        DriverManagerDataSource configured = new DriverManagerDataSource();
        configured.setDriverClassName("org.h2.Driver");
        configured.setUrl("jdbc:h2:mem:business_release_" + UUID.randomUUID()
                + ";MODE=MySQL;DB_CLOSE_DELAY=-1");
        DataSource dataSource = configured;
        jdbc = new DatabaseClockJdbcTemplate(dataSource);
        createSchema();
        seedDevices();

        DeviceScopeAuthorizationPort authorization =
                mock(DeviceScopeAuthorizationPort.class);
        AuthorizedDeviceScope platformActor = actor();
        when(authorization.authorize(any())).thenReturn(platformActor);
        verifier = mock(BusinessReleasePackageVerifier.class);
        BusinessReleaseSigningKeyPort signingKeys =
                mock(BusinessReleaseSigningKeyPort.class);
        when(signingKeys.readiness()).thenReturn(
                new BusinessReleaseSigningKeyPort.Readiness(
                        true, "验签公钥目录可用"));
        service = new BusinessReleaseControlPlaneService(
                jdbc,
                authorization,
                new MemoryArtifactStorage(),
                signingKeys,
                verifier,
                new DeviceConfigurationCanonicalizer(),
                new DataSourceTransactionManager(dataSource),
                false);
    }

    @Test
    void releaseApprovalAndRolloutPlanningNeverDispatchDeviceWork()
            throws Exception {
        UUID createOperation = UUID.randomUUID();
        var draft = service.createDraft(
                createOperation,
                new CreateReleaseDraftRequest("2.0.0", "业务逻辑更新"));
        assertThat(draft.statusLabel()).isEqualTo("草稿");
        assertThat(draft.releaseSequence()).isEqualTo(2);
        assertThat(service.createDraft(
                createOperation,
                new CreateReleaseDraftRequest("2.0.0", "业务逻辑更新"))
                .releaseUid()).isEqualTo(draft.releaseUid());

        byte[] packageBytes = "offline-signed-business-package"
                .getBytes(java.nio.charset.StandardCharsets.UTF_8);
        Path packagePath = temporary.resolve("package.tar.gz");
        Path signaturePath = temporary.resolve("package.sig");
        Files.write(packagePath, packageBytes);
        Files.write(signaturePath, new byte[64]);
        String packageSha = sha256(packageBytes);
        UUID uploadOperation = UUID.randomUUID();
        var uploaded = service.uploadArtifacts(
                uploadOperation,
                draft.releaseUid(),
                packagePath,
                signaturePath,
                "production-2026",
                "上传离线签名制品");
        assertThat(uploaded.artifactUploaded()).isTrue();
        Path changedPackage = temporary.resolve("changed-package.tar.gz");
        Files.write(changedPackage, "offline-signed-business-packagf"
                .getBytes(java.nio.charset.StandardCharsets.UTF_8));
        assertThatThrownBy(() -> service.uploadArtifacts(
                uploadOperation,
                draft.releaseUid(),
                changedPackage,
                signaturePath,
                "production-2026",
                "上传离线签名制品"))
                .isInstanceOf(TargetApiException.class)
                .hasMessageContaining("不同的业务发布制品");
        assertThat(service.uploadArtifacts(
                uploadOperation,
                draft.releaseUid(),
                packagePath,
                signaturePath,
                "production-2026",
                "上传离线签名制品").packageSha256())
                .isEqualTo(packageSha);

        when(verifier.verify(
                any(Path.class),
                any(Path.class),
                anyString(),
                any(UUID.class),
                anyString(),
                anyLong())).thenAnswer(invocation -> new VerifiedRelease(
                invocation.getArgument(3),
                invocation.getArgument(4),
                invocation.getArgument(5),
                packageSha,
                (long) packageBytes.length,
                1,
                2,
                2,
                1,
                0,
                1,
                0,
                "FIXED_FRAME",
                null,
                null,
                2,
                "0000000000000000",
                "0000000000000000",
                Map.of()));

        var verified = service.verify(
                UUID.randomUUID(), draft.releaseUid(), "核对发布包");
        assertThat(verified.statusLabel()).isEqualTo("等待批准");
        assertThat(verified.compatibility().uartProtocol())
                .isEqualTo("固定帧修订 2");

        UUID approvalOperation = UUID.randomUUID();
        var approved = service.approve(
                approvalOperation, draft.releaseUid(), "批准进入灰度演练");
        assertThat(approved.statusLabel()).isEqualTo("已批准，可创建计划");
        assertThatThrownBy(() -> service.approve(
                approvalOperation, draft.releaseUid(), "更换批准原因"))
                .isInstanceOf(TargetApiException.class)
                .hasMessageContaining("幂等键");

        UUID rolloutOperation = UUID.randomUUID();
        var rollout = service.createRollout(
                rolloutOperation,
                new CreateRolloutRequest(
                        draft.releaseUid(),
                        VALIDATION_SN,
                        List.of(WAVE_SN),
                        1,
                        "先验证一台再分批"));
        assertThat(rollout.statusLabel()).isEqualTo("计划已建立，尚未下发");
        assertThat(rollout.remoteDispatchEnabled()).isFalse();
        assertThat(rollout.deployments())
                .extracting(item -> item.statusLabel())
                .containsOnly("已规划，尚未下发");
        assertThat(rollout.deployments())
                .extracting(item -> item.kindLabel())
                .containsExactly("验证设备", "灰度批次设备");
        assertThat(service.createRollout(
                rolloutOperation,
                new CreateRolloutRequest(
                        draft.releaseUid(),
                        VALIDATION_SN,
                        List.of(WAVE_SN),
                        1,
                        "先验证一台再分批")).rolloutUid())
                .isEqualTo(rollout.rolloutUid());

        var stopped = service.stopRollout(
                UUID.randomUUID(), rollout.rolloutUid(), "结束本次演练");
        assertThat(stopped.statusLabel()).isEqualTo("计划已停止");
        assertThat(jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_edge_software_deployment",
                Long.class)).isEqualTo(2);
    }

    @Test
    void staleOrUnhealthyActualFactRejectsTheWholePlanBeforeRowsAreCreated()
            throws Exception {
        var ready = readyRelease();
        jdbc.update("""
                UPDATE dev_device_software_fact
                SET business_process_ready = FALSE
                WHERE asset_id = 2
                """);

        assertThatThrownBy(() -> service.createRollout(
                UUID.randomUUID(),
                new CreateRolloutRequest(
                        ready.releaseUid(),
                        VALIDATION_SN,
                        List.of(WAVE_SN),
                        3,
                        "必须全部满足安装前检查")))
                .isInstanceOf(TargetApiException.class)
                .hasMessageContaining("业务程序当前没有正常运行");
        assertThat(jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_edge_software_rollout",
                Long.class)).isZero();
        assertThat(jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_edge_software_deployment",
                Long.class)).isZero();
    }

    @Test
    void defaultConfigurationKeepsRemoteDispatchDisabled() {
        assertThat(service.readiness().remoteDispatchEnabled()).isFalse();
    }

    @Test
    void artifactIoEntryPointsAuthorizeInsideShortTransactions()
            throws Exception {
        var draft = service.createDraft(
                UUID.randomUUID(),
                new CreateReleaseDraftRequest(
                        "2.0.0-transaction", null));
        byte[] packageBytes = "transaction-bound-authorization"
                .getBytes(java.nio.charset.StandardCharsets.UTF_8);
        Path packagePath = temporary.resolve("transaction-package.tar.gz");
        Path signaturePath = temporary.resolve("transaction-package.sig");
        Files.write(packagePath, packageBytes);
        Files.write(signaturePath, new byte[64]);
        String packageSha = sha256(packageBytes);

        AtomicInteger authorizationCount = new AtomicInteger();
        DeviceScopeAuthorizationPort transactionRequiredAuthorization = query -> {
            if (!TransactionSynchronizationManager
                    .isActualTransactionActive()) {
                throw new IllegalStateException(
                        "platform authorization requires a transaction");
            }
            authorizationCount.incrementAndGet();
            return actor();
        };
        MemoryArtifactStorage storage = new MemoryArtifactStorage();
        BusinessReleasePackageVerifier localVerifier =
                mock(BusinessReleasePackageVerifier.class);
        when(localVerifier.verify(
                any(Path.class),
                any(Path.class),
                anyString(),
                any(UUID.class),
                anyString(),
                anyLong())).thenAnswer(invocation -> new VerifiedRelease(
                invocation.getArgument(3),
                invocation.getArgument(4),
                invocation.getArgument(5),
                packageSha,
                (long) packageBytes.length,
                1,
                2,
                2,
                1,
                0,
                1,
                0,
                "FIXED_FRAME",
                null,
                null,
                2,
                "0000000000000000",
                "0000000000000000",
                Map.of()));
        BusinessReleaseControlPlaneService transactionAware =
                new BusinessReleaseControlPlaneService(
                        jdbc,
                        transactionRequiredAuthorization,
                        storage,
                        mock(BusinessReleaseSigningKeyPort.class),
                        localVerifier,
                        new DeviceConfigurationCanonicalizer(),
                        new DataSourceTransactionManager(jdbc.getDataSource()),
                        false);

        assertThat(transactionAware.uploadArtifacts(
                UUID.randomUUID(),
                draft.releaseUid(),
                packagePath,
                signaturePath,
                "business_2026",
                "上传需要事务授权的制品").artifactUploaded()).isTrue();
        assertThat(transactionAware.verify(
                UUID.randomUUID(),
                draft.releaseUid(),
                "校验需要事务授权的制品").status()).isEqualTo(
                        "AWAITING_APPROVAL");
        assertThat(authorizationCount).hasValue(2);
    }

    @Test
    void stageEightMayExposeRemoteReadinessWithoutDispatchingOnConstruction() {
        DeviceScopeAuthorizationPort authorization =
                mock(DeviceScopeAuthorizationPort.class);
        AuthorizedDeviceScope platformActor = actor();
        when(authorization.authorize(any())).thenReturn(platformActor);
        BusinessReleaseSigningKeyPort signingKeys =
                mock(BusinessReleaseSigningKeyPort.class);
        when(signingKeys.readiness()).thenReturn(
                new BusinessReleaseSigningKeyPort.Readiness(
                        true, "验签公钥目录可用"));
        BusinessReleaseControlPlaneService remote =
                new BusinessReleaseControlPlaneService(
                jdbc,
                authorization,
                new MemoryArtifactStorage(),
                signingKeys,
                verifier,
                new DeviceConfigurationCanonicalizer(),
                new DataSourceTransactionManager(jdbc.getDataSource()),
                true);

        assertThat(remote.readiness().remoteDispatchEnabled()).isTrue();
    }

    @Test
    void remoteValidationRegistersOneFrozenTaskAndPausesOnlyValidationDevice()
            throws Exception {
        DeviceScopeAuthorizationPort authorization =
                mock(DeviceScopeAuthorizationPort.class);
        AuthorizedDeviceScope platformActor = actor();
        when(authorization.authorize(any())).thenReturn(platformActor);
        BusinessReleaseSigningKeyPort signingKeys =
                mock(BusinessReleaseSigningKeyPort.class);
        when(signingKeys.readiness()).thenReturn(
                new BusinessReleaseSigningKeyPort.Readiness(
                        true, "验签公钥目录可用"));
        PlatformDeviceAssetTaskRefFactory taskRefs =
                mock(PlatformDeviceAssetTaskRefFactory.class);
        when(taskRefs.issue(anyLong())).thenReturn(
                mock(PlatformDeviceAssetTaskRef.class));
        ReliablePlatformDeviceControlTaskRegistrationPort registrations =
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class);
        List<ReliablePlatformDeviceControlTaskRegistration> registered =
                new ArrayList<>();
        UUID taskUid = UUID.randomUUID();
        when(registrations.register(any())).thenAnswer(invocation -> {
            registered.add(invocation.getArgument(0));
            return taskUid;
        });
        JsonMapper objectMapper = JsonMapper.builder().build();
        ReliableDeviceTaskProofPort taskProof =
                mock(ReliableDeviceTaskProofPort.class);
        ReliableTaskWakePort taskWake = mock(ReliableTaskWakePort.class);
        service = new BusinessReleaseControlPlaneService(
                jdbc,
                authorization,
                new MemoryArtifactStorage(),
                signingKeys,
                verifier,
                new DeviceConfigurationCanonicalizer(),
                new DataSourceTransactionManager(jdbc.getDataSource()),
                objectMapper,
                taskRefs,
                registrations,
                taskProof,
                taskWake,
                true);

        var release = readyRelease();
        var rollout = service.createRollout(
                UUID.randomUUID(),
                new CreateRolloutRequest(
                        release.releaseUid(),
                        VALIDATION_SN,
                        List.of(WAVE_SN),
                        1,
                        "先验证一台设备"));
        UUID operationUid = UUID.randomUUID();
        var validating = service.startValidation(
                operationUid, rollout.rolloutUid(), "开始单设备验证");

        assertThat(validating.status()).isEqualTo("VALIDATING");
        assertThat(validating.statusLabel()).isEqualTo("验证设备正在更新");
        assertThat(validating.deployments())
                .extracting(item -> item.status())
                .containsExactly("QUEUED", "PLANNED");
        assertThat(validating.deployments().getFirst().statusLabel())
                .isEqualTo("更新命令等待发送");
        assertThat(validating.deployments().getFirst().businessAdmissionLabel())
                .isEqualTo("新业务已暂停，等待设备状态恢复");
        assertThat(registered).hasSize(1);

        ReliablePlatformDeviceControlTaskRegistration registration =
                registered.getFirst();
        assertThat(registration.taskType())
                .isEqualTo("START_BUSINESS_RUNTIME_UPDATE");
        assertThat(registration.targetType())
                .isEqualTo("BUSINESS_RUNTIME_DEPLOYMENT");
        assertThat(registration.maxAutoAttempts()).isEqualTo(20);
        JsonNode envelope = objectMapper.readTree(registration.executionEnvelope());
        assertThat(envelope.path("targetDeviceName").asText())
                .isEqualTo(VALIDATION_SN);
        assertThat(envelope.path("cosGrant").isNull()).isTrue();
        assertThat(envelope.path("downloadGrant").isNull()).isTrue();
        assertThat(envelope.path("payload").path("objectKey").asText())
                .isEqualTo("edge-runtime/releases/"
                        + release.releaseUid() + "/package.tar.gz");
        assertThat(registration.executionEnvelope())
                .doesNotContain("https://", "http://", "X-Amz-");

        var replay = service.startValidation(
                operationUid, rollout.rolloutUid(), "开始单设备验证");
        assertThat(replay.rolloutUid()).isEqualTo(rollout.rolloutUid());
        assertThat(registered).hasSize(1);
        assertThat(jdbc.queryForObject("""
                SELECT business_admission_status
                FROM dev_device_compatibility_projection
                WHERE asset_id = 1
                """, String.class)).isEqualTo("PAUSED");
        assertThat(jdbc.queryForObject("""
                SELECT business_admission_status
                FROM dev_device_compatibility_projection
                WHERE asset_id = 2
                """, String.class)).isEqualTo("ACCEPTING");

        UUID deploymentUid = validating.deployments()
                .getFirst().deploymentUid();
        UUID commandUid = UUID.fromString(jdbc.queryForObject("""
                SELECT command_uid
                FROM dev_edge_software_deployment
                WHERE deployment_uid = ?
                """, String.class, deploymentUid.toString()));
        UUID updateUid = UUID.fromString(jdbc.queryForObject("""
                SELECT edge_update_uid
                FROM dev_edge_software_deployment
                WHERE deployment_uid = ?
                """, String.class, deploymentUid.toString()));
        InstalledIdentity targetIdentity = new InstalledIdentity(
                release.releaseUid(),
                release.versionName(),
                release.releaseSequence(),
                release.packageSha256());
        assertThatThrownBy(() -> service.applyProgress(businessProgress(
                objectMapper,
                release,
                deploymentUid,
                commandUid,
                updateUid,
                "ROLLED_BACK",
                1,
                targetIdentity,
                true,
                null,
                100)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("frozen source identity");

        assertThat(service.applyProgress(businessProgress(
                objectMapper,
                release,
                deploymentUid,
                commandUid,
                updateUid,
                "DOWNLOAD_AUTHORIZATION_REQUIRED",
                1,
                null,
                false,
                "DOWNLOAD_AUTHORIZATION_EXPIRED",
                101))).isEqualTo(TrustedDeviceEventApplyResult.APPLIED);
        verify(taskWake).wake(any());

        assertThat(service.applyProgress(businessProgress(
                objectMapper,
                release,
                deploymentUid,
                commandUid,
                updateUid,
                "SUCCEEDED",
                2,
                targetIdentity,
                false,
                null,
                102))).isEqualTo(TrustedDeviceEventApplyResult.APPLIED);
        verify(taskProof).completeFromTrustedProof(
                "START_BUSINESS_RUNTIME_UPDATE",
                "BUSINESS_RUNTIME_DEPLOYMENT",
                deploymentUid.toString());
        var completed = service.rolloutDetail(rollout.rolloutUid());
        assertThat(completed.status()).isEqualTo("AWAITING_PROMOTION");
        assertThat(completed.deployments().getFirst().installedVersionName())
                .isEqualTo(release.versionName());
        assertThat(completed.deployments().getFirst().statusLabel())
                .isEqualTo("业务程序更新成功");
    }

    @Test
    void cancellationWaitsForTheDeviceResultBeforeStoppingValidation()
            throws Exception {
        DeviceScopeAuthorizationPort authorization =
                mock(DeviceScopeAuthorizationPort.class);
        AuthorizedDeviceScope platformActor = actor();
        when(authorization.authorize(any())).thenReturn(platformActor);
        BusinessReleaseSigningKeyPort signingKeys =
                mock(BusinessReleaseSigningKeyPort.class);
        when(signingKeys.readiness()).thenReturn(
                new BusinessReleaseSigningKeyPort.Readiness(
                        true, "验签公钥目录可用"));
        PlatformDeviceAssetTaskRefFactory taskRefs =
                mock(PlatformDeviceAssetTaskRefFactory.class);
        when(taskRefs.issue(anyLong())).thenReturn(
                mock(PlatformDeviceAssetTaskRef.class));
        ReliablePlatformDeviceControlTaskRegistrationPort registrations =
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class);
        List<ReliablePlatformDeviceControlTaskRegistration> registered =
                new ArrayList<>();
        when(registrations.register(any())).thenAnswer(invocation -> {
            registered.add(invocation.getArgument(0));
            return UUID.randomUUID();
        });
        JsonMapper objectMapper = JsonMapper.builder().build();
        ReliableDeviceTaskProofPort taskProof =
                mock(ReliableDeviceTaskProofPort.class);
        service = new BusinessReleaseControlPlaneService(
                jdbc,
                authorization,
                new MemoryArtifactStorage(),
                signingKeys,
                verifier,
                new DeviceConfigurationCanonicalizer(),
                new DataSourceTransactionManager(jdbc.getDataSource()),
                objectMapper,
                taskRefs,
                registrations,
                taskProof,
                mock(ReliableTaskWakePort.class),
                true);

        ReleaseView release = readyRelease();
        var rollout = service.createRollout(
                UUID.randomUUID(),
                new CreateRolloutRequest(
                        release.releaseUid(),
                        VALIDATION_SN,
                        List.of(),
                        3,
                        "验证取消边界"));
        var validating = service.startValidation(
                UUID.randomUUID(), rollout.rolloutUid(), "开始验证");
        UUID deploymentUid = validating.deployments().getFirst()
                .deploymentUid();
        UUID updateUid = UUID.fromString(jdbc.queryForObject("""
                SELECT edge_update_uid
                FROM dev_edge_software_deployment
                WHERE deployment_uid = ?
                """, String.class, deploymentUid.toString()));

        UUID cancelOperationUid = UUID.randomUUID();
        var pending = service.cancelDeployment(
                cancelOperationUid,
                rollout.rolloutUid(),
                deploymentUid,
                "验证设备现场异常，停止继续更新");

        assertThat(pending.status()).isEqualTo("VALIDATING");
        assertThat(pending.deployments().getFirst().status())
                .isEqualTo("QUEUED");
        assertThat(pending.deployments().getFirst().cancellationStatus())
                .isEqualTo("QUEUED");
        assertThat(pending.deployments().getFirst().cancellationStatusLabel())
                .isEqualTo("已请求取消，等待设备确认");
        assertThat(registered).hasSize(2);
        ReliablePlatformDeviceControlTaskRegistration cancelRegistration =
                registered.get(1);
        assertThat(cancelRegistration.taskType())
                .isEqualTo("CANCEL_BUSINESS_RUNTIME_UPDATE");
        JsonNode cancelEnvelope = objectMapper.readTree(
                cancelRegistration.executionEnvelope());
        assertThat(cancelEnvelope.path("payload").path("controlSequence")
                .asLong()).isEqualTo(2);
        assertThat(cancelEnvelope.path("cosGrant").isNull()).isTrue();
        assertThat(cancelEnvelope.has("downloadGrant")).isFalse();
        assertThat(cancelRegistration.executionEnvelope())
                .doesNotContain("https://", "http://", "secret", "token");

        service.cancelDeployment(
                cancelOperationUid,
                rollout.rolloutUid(),
                deploymentUid,
                "验证设备现场异常，停止继续更新");
        assertThat(registered).hasSize(2);

        UUID cancelCommandUid = UUID.fromString(jdbc.queryForObject("""
                SELECT cancel_command_uid
                FROM dev_edge_software_deployment
                WHERE deployment_uid = ?
                """, String.class, deploymentUid.toString()));
        assertThat(service.applyCancellationResult(businessCancellationResult(
                objectMapper,
                deploymentUid,
                cancelCommandUid,
                updateUid,
                "CANCELLED",
                "WAITING_FOR_IDLE",
                "OPEN",
                null,
                201))).isEqualTo(TrustedDeviceEventApplyResult.APPLIED);

        var cancelled = service.rolloutDetail(rollout.rolloutUid());
        assertThat(cancelled.status()).isEqualTo("STOPPED");
        assertThat(cancelled.stopReason())
                .isEqualTo("验证设备现场异常，停止继续更新");
        assertThat(cancelled.deployments().getFirst().status())
                .isEqualTo("CANCELLED");
        assertThat(cancelled.deployments().getFirst().statusLabel())
                .isEqualTo("设备已安全取消本次更新");
        assertThat(cancelled.deployments().getFirst().cancellationStatus())
                .isEqualTo("CANCELLED");
        assertThat(jdbc.queryForObject("""
                SELECT business_admission_status
                FROM dev_device_compatibility_projection
                WHERE asset_id = 1
                """, String.class)).isEqualTo("ACCEPTING");
        verify(taskProof).completeFromTrustedProof(
                "CANCEL_BUSINESS_RUNTIME_UPDATE",
                "BUSINESS_RUNTIME_DEPLOYMENT",
                deploymentUid.toString());
        verify(taskProof).completeFromTrustedProof(
                "START_BUSINESS_RUNTIME_UPDATE",
                "BUSINESS_RUNTIME_DEPLOYMENT",
                deploymentUid.toString());
    }

    @Test
    void tooLateCancellationLeavesTheUpdateRunningUntilItsRealResult()
            throws Exception {
        RemoteTestContext remote = configureRemoteControlPlane();
        ReleaseView release = readyRelease();
        var rollout = service.createRollout(
                UUID.randomUUID(),
                new CreateRolloutRequest(
                        release.releaseUid(),
                        VALIDATION_SN,
                        List.of(),
                        3,
                        "验证无法中断的切换阶段"));
        var validating = service.startValidation(
                UUID.randomUUID(), rollout.rolloutUid(), "开始验证");
        UUID deploymentUid = validating.deployments().getFirst()
                .deploymentUid();
        UUID commandUid = UUID.fromString(jdbc.queryForObject("""
                SELECT command_uid
                FROM dev_edge_software_deployment
                WHERE deployment_uid = ?
                """, String.class, deploymentUid.toString()));
        UUID updateUid = UUID.fromString(jdbc.queryForObject("""
                SELECT edge_update_uid
                FROM dev_edge_software_deployment
                WHERE deployment_uid = ?
                """, String.class, deploymentUid.toString()));
        service.cancelDeployment(
                UUID.randomUUID(),
                rollout.rolloutUid(),
                deploymentUid,
                "设备已经开始切换前尝试停止");
        UUID cancelCommandUid = UUID.fromString(jdbc.queryForObject("""
                SELECT cancel_command_uid
                FROM dev_edge_software_deployment
                WHERE deployment_uid = ?
                """, String.class, deploymentUid.toString()));

        assertThat(service.applyCancellationResult(businessCancellationResult(
                remote.objectMapper(),
                deploymentUid,
                cancelCommandUid,
                updateUid,
                "TOO_LATE",
                "MIGRATING_DATA",
                "MAINTENANCE",
                "BUSINESS_UPDATE_CANCEL_TOO_LATE",
                202))).isEqualTo(TrustedDeviceEventApplyResult.APPLIED);

        var continuing = service.rolloutDetail(rollout.rolloutUid());
        assertThat(continuing.status()).isEqualTo("VALIDATING");
        assertThat(continuing.deployments().getFirst().cancellationStatus())
                .isEqualTo("TOO_LATE");
        assertThat(continuing.deployments().getFirst().cancellationStatusLabel())
                .isEqualTo("设备已开始切换，无法取消");
        assertThat(jdbc.queryForObject("""
                SELECT business_admission_status
                FROM dev_device_compatibility_projection
                WHERE asset_id = 1
                """, String.class)).isEqualTo("PAUSED");

        InstalledIdentity targetIdentity = new InstalledIdentity(
                release.releaseUid(),
                release.versionName(),
                release.releaseSequence(),
                release.packageSha256());
        assertThat(service.applyProgress(businessProgress(
                remote.objectMapper(),
                release,
                deploymentUid,
                commandUid,
                updateUid,
                "SUCCEEDED",
                1,
                targetIdentity,
                false,
                null,
                203))).isEqualTo(TrustedDeviceEventApplyResult.APPLIED);
        var completed = service.rolloutDetail(rollout.rolloutUid());
        assertThat(completed.status()).isEqualTo("AWAITING_PROMOTION");
        assertThat(completed.deployments().getFirst().status())
                .isEqualTo("SUCCEEDED");
        assertThat(completed.deployments().getFirst().cancellationStatus())
                .isEqualTo("TOO_LATE");
        verify(remote.taskProof()).completeFromTrustedProof(
                "CANCEL_BUSINESS_RUNTIME_UPDATE",
                "BUSINESS_RUNTIME_DEPLOYMENT",
                deploymentUid.toString());
        verify(remote.taskProof()).completeFromTrustedProof(
                "START_BUSINESS_RUNTIME_UPDATE",
                "BUSINESS_RUNTIME_DEPLOYMENT",
                deploymentUid.toString());
    }

    @Test
    void staleVerificationWorkerCannotOverwriteTheNewVerificationResult()
            throws Exception {
        var draft = service.createDraft(
                UUID.randomUUID(),
                new CreateReleaseDraftRequest("2.1.0", null));
        byte[] bytes = "stale-verification-package".getBytes(
                java.nio.charset.StandardCharsets.UTF_8);
        Path packagePath = temporary.resolve("stale-package.tar.gz");
        Path signaturePath = temporary.resolve("stale-package.sig");
        Files.write(packagePath, bytes);
        Files.write(signaturePath, new byte[64]);
        service.uploadArtifacts(
                UUID.randomUUID(), draft.releaseUid(), packagePath,
                signaturePath, "production-2026", "上传");
        String digest = sha256(bytes);
        CountDownLatch oldWorkerStarted = new CountDownLatch(1);
        CountDownLatch finishOldWorker = new CountDownLatch(1);
        when(verifier.verify(
                any(Path.class), any(Path.class), anyString(),
                any(UUID.class), anyString(), anyLong()))
                .thenAnswer(invocation -> {
                    if (Thread.currentThread().getName()
                            .startsWith("old-release-verifier")) {
                        oldWorkerStarted.countDown();
                        if (!finishOldWorker.await(10, TimeUnit.SECONDS)) {
                            throw new IllegalStateException(
                                    "timed out waiting for replacement verifier");
                        }
                    }
                    return verifiedRelease(invocation, digest, bytes.length);
                });
        UUID oldOperation = UUID.randomUUID();
        ExecutorService executor = Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable);
            thread.setName("old-release-verifier");
            return thread;
        });
        try {
            var oldResult = executor.submit(() -> service.verify(
                    oldOperation, draft.releaseUid(), "第一次校验"));
            assertThat(oldWorkerStarted.await(5, TimeUnit.SECONDS)).isTrue();
            jdbc.update("""
                    UPDATE dev_edge_software_release_control
                    SET updated_at = ?
                    WHERE release_uid = ?
                    """, LocalDateTime.now(ZoneOffset.UTC).minusMinutes(31),
                    draft.releaseUid().toString());

            var replacement = service.verify(
                    UUID.randomUUID(), draft.releaseUid(), "接管超时校验");
            assertThat(replacement.statusLabel()).isEqualTo("等待批准");
            finishOldWorker.countDown();
            assertThat(oldResult.get(5, TimeUnit.SECONDS).statusLabel())
                    .isEqualTo("等待批准");
        } finally {
            finishOldWorker.countDown();
            executor.shutdownNow();
        }
        assertThat(jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_edge_software_release",
                Long.class)).isEqualTo(1);
        assertThat(jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM dev_edge_software_release_action
                WHERE action_type = 'VERIFICATION_PASSED'
                """, Long.class)).isEqualTo(1);
    }

    private RemoteTestContext configureRemoteControlPlane() {
        DeviceScopeAuthorizationPort authorization =
                mock(DeviceScopeAuthorizationPort.class);
        AuthorizedDeviceScope platformActor = actor();
        when(authorization.authorize(any())).thenReturn(platformActor);
        BusinessReleaseSigningKeyPort signingKeys =
                mock(BusinessReleaseSigningKeyPort.class);
        when(signingKeys.readiness()).thenReturn(
                new BusinessReleaseSigningKeyPort.Readiness(
                        true, "验签公钥目录可用"));
        PlatformDeviceAssetTaskRefFactory taskRefs =
                mock(PlatformDeviceAssetTaskRefFactory.class);
        when(taskRefs.issue(anyLong())).thenReturn(
                mock(PlatformDeviceAssetTaskRef.class));
        ReliablePlatformDeviceControlTaskRegistrationPort registrations =
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class);
        List<ReliablePlatformDeviceControlTaskRegistration> registered =
                new ArrayList<>();
        when(registrations.register(any())).thenAnswer(invocation -> {
            registered.add(invocation.getArgument(0));
            return UUID.randomUUID();
        });
        JsonMapper objectMapper = JsonMapper.builder().build();
        ReliableDeviceTaskProofPort taskProof =
                mock(ReliableDeviceTaskProofPort.class);
        service = new BusinessReleaseControlPlaneService(
                jdbc,
                authorization,
                new MemoryArtifactStorage(),
                signingKeys,
                verifier,
                new DeviceConfigurationCanonicalizer(),
                new DataSourceTransactionManager(jdbc.getDataSource()),
                objectMapper,
                taskRefs,
                registrations,
                taskProof,
                mock(ReliableTaskWakePort.class),
                true);
        return new RemoteTestContext(objectMapper, registered, taskProof);
    }

    private org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels
            .ReleaseView readyRelease() throws Exception {
        var draft = service.createDraft(
                UUID.randomUUID(),
                new CreateReleaseDraftRequest("2.0.0", null));
        byte[] bytes = "package".getBytes(
                java.nio.charset.StandardCharsets.UTF_8);
        Path packagePath = temporary.resolve("ready-package.tar.gz");
        Path signaturePath = temporary.resolve("ready-package.sig");
        Files.write(packagePath, bytes);
        Files.write(signaturePath, new byte[64]);
        service.uploadArtifacts(
                UUID.randomUUID(), draft.releaseUid(), packagePath,
                signaturePath, "production-2026", "上传");
        String digest = sha256(bytes);
        when(verifier.verify(
                any(Path.class), any(Path.class), anyString(),
                any(UUID.class), anyString(), anyLong()))
                .thenAnswer(invocation -> new VerifiedRelease(
                        invocation.getArgument(3), invocation.getArgument(4),
                        invocation.getArgument(5), digest, (long) bytes.length,
                        1, 2, 2, 1, 0, 1, 0,
                        "FIXED_FRAME", null, null, 2,
                        "0000000000000000", "0000000000000000", Map.of()));
        service.verify(UUID.randomUUID(), draft.releaseUid(), "校验");
        return service.approve(
                UUID.randomUUID(), draft.releaseUid(), "批准");
    }

    private static VerifiedRelease verifiedRelease(
            org.mockito.invocation.InvocationOnMock invocation,
            String digest,
            long size) {
        return new VerifiedRelease(
                invocation.getArgument(3), invocation.getArgument(4),
                invocation.getArgument(5), digest, size,
                1, 2, 2, 1, 0, 1, 0,
                "FIXED_FRAME", null, null, 2,
                "0000000000000000", "0000000000000000", Map.of());
    }

    private TrustedPlatformDeviceAssetFactEvent businessProgress(
            JsonMapper objectMapper,
            ReleaseView release,
            UUID deploymentUid,
            UUID commandUid,
            UUID updateUid,
            String stage,
            long stageSequence,
            InstalledIdentity installed,
            boolean databaseRestored,
            String errorCode,
            long sourceInboxId) {
        ObjectNode normalized = objectMapper.createObjectNode();
        normalized.putObject("trustedSource")
                .put("deviceName", VALIDATION_SN);
        ObjectNode event = normalized.putObject("event");
        event.put("eventUid", UUID.randomUUID().toString());
        event.put("eventType", BusinessReleaseControlPlaneService.EVENT_TYPE);
        event.put("commandUid", commandUid.toString());
        event.put("occurredAt", Instant.now().toString());
        event.put("payloadSha256", "d".repeat(64));
        event.putObject("target")
                .put("type", "BUSINESS_RUNTIME_DEPLOYMENT")
                .put("uid", deploymentUid.toString());
        ObjectNode payload = event.putObject("payload");
        payload.put("deploymentUid", deploymentUid.toString());
        payload.put("updateUid", updateUid.toString());
        payload.put("releaseUid", release.releaseUid().toString());
        payload.put("versionName", release.versionName());
        payload.put("releaseSequence", release.releaseSequence());
        payload.put("packageSha256", release.packageSha256());
        payload.put("stage", stage);
        payload.put("stageSequence", stageSequence);
        payload.put(
                "businessAdmissionState",
                "SUCCEEDED".equals(stage) ? "OPEN" : "LOCKED");
        payload.put("downloadAttemptCount", 1);
        payload.put(
                "targetAttemptCount",
                "SUCCEEDED".equals(stage) ? 1 : 0);
        payload.put(
                "rollbackAttemptCount",
                "ROLLED_BACK".equals(stage) ? 1 : 0);
        payload.put("databaseRestored", databaseRestored);
        if (installed == null) {
            payload.putNull("installedReleaseUid");
            payload.putNull("installedVersionName");
            payload.putNull("installedReleaseSequence");
            payload.putNull("installedPackageSha256");
        } else {
            payload.put("installedReleaseUid", installed.releaseUid().toString());
            payload.put("installedVersionName", installed.versionName());
            payload.put("installedReleaseSequence", installed.releaseSequence());
            payload.put("installedPackageSha256", installed.packageSha256());
        }
        if (errorCode == null) {
            payload.putNull("errorCode");
        } else {
            payload.put("errorCode", errorCode);
        }
        TrustedPlatformInboxRef sourceInbox =
                mock(TrustedPlatformInboxRef.class);
        when(sourceInbox.use(any())).thenAnswer(invocation -> {
            TrustedPlatformInboxRef.PlatformInboxFunction<Object> function =
                    invocation.getArgument(0);
            return function.apply(sourceInboxId);
        });
        return new TrustedPlatformDeviceAssetFactEvent(
                sourceInbox,
                BusinessReleaseControlPlaneService.EVENT_TYPE,
                2,
                objectMapper.writeValueAsString(normalized));
    }

    private TrustedPlatformDeviceAssetFactEvent businessCancellationResult(
            JsonMapper objectMapper,
            UUID deploymentUid,
            UUID cancelCommandUid,
            UUID updateUid,
            String result,
            String observedStage,
            String admission,
            String errorCode,
            long sourceInboxId) {
        ObjectNode normalized = objectMapper.createObjectNode();
        normalized.putObject("trustedSource")
                .put("deviceName", VALIDATION_SN);
        ObjectNode event = normalized.putObject("event");
        event.put("eventUid", UUID.randomUUID().toString());
        event.put(
                "eventType",
                BusinessReleaseControlPlaneService.CANCEL_EVENT_TYPE);
        event.put("commandUid", cancelCommandUid.toString());
        event.put("occurredAt", Instant.now().toString());
        event.put("payloadSha256", "e".repeat(64));
        event.putObject("target")
                .put("type", "BUSINESS_RUNTIME_DEPLOYMENT")
                .put("uid", deploymentUid.toString());
        ObjectNode payload = event.putObject("payload");
        payload.put("deploymentUid", deploymentUid.toString());
        payload.put("updateUid", updateUid.toString());
        payload.put("controlSequence", 2);
        payload.put("result", result);
        payload.put("observedStage", observedStage);
        payload.put("businessAdmissionState", admission);
        if (errorCode == null) {
            payload.putNull("errorCode");
        } else {
            payload.put("errorCode", errorCode);
        }
        TrustedPlatformInboxRef sourceInbox =
                mock(TrustedPlatformInboxRef.class);
        when(sourceInbox.use(any())).thenAnswer(invocation -> {
            TrustedPlatformInboxRef.PlatformInboxFunction<Object> function =
                    invocation.getArgument(0);
            return function.apply(sourceInboxId);
        });
        return new TrustedPlatformDeviceAssetFactEvent(
                sourceInbox,
                BusinessReleaseControlPlaneService.CANCEL_EVENT_TYPE,
                2,
                objectMapper.writeValueAsString(normalized));
    }

    private AuthorizedDeviceScope actor() {
        DeviceScopePersistenceRef persistence =
                mock(DeviceScopePersistenceRef.class);
        doAnswer(invocation -> {
            DeviceScopePersistenceRef.ForeignKeyWriter writer =
                    invocation.getArgument(0);
            writer.write(null, null, 9L, null);
            return null;
        }).when(persistence).writeForeignKeysTo(any());
        return new AuthorizedDeviceScope(
                true,
                UUID.randomUUID(),
                UUID.randomUUID(),
                "发布管理员",
                null,
                null,
                true,
                true,
                persistence);
    }

    private void createSchema() {
        jdbc.execute("""
                CREATE TABLE iam_platform_admin (
                    id BIGINT PRIMARY KEY,
                    display_name VARCHAR(100) NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE iam_tenant (
                    id BIGINT PRIMARY KEY,
                    tenant_code VARCHAR(64) NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE iam_organization (
                    id BIGINT PRIMARY KEY,
                    tenant_id BIGINT NOT NULL,
                    organization_code VARCHAR(64) NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_device_asset (
                    id BIGINT PRIMARY KEY,
                    hardware_sn VARCHAR(64) NOT NULL UNIQUE,
                    tenant_id BIGINT,
                    organization_id BIGINT,
                    lifecycle_status VARCHAR(16) NOT NULL,
                    acceptance_status VARCHAR(16) NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_device_management_profile (
                    asset_id BIGINT PRIMARY KEY,
                    architecture_generation VARCHAR(24) NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_device_software_fact (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    asset_id BIGINT NOT NULL,
                    management_state_sequence BIGINT NOT NULL,
                    business_gate_state VARCHAR(16) NOT NULL,
                    communication_business_protocol_major INT NOT NULL,
                    communication_business_protocol_minor INT NOT NULL,
                    updater_business_protocol_major INT NOT NULL,
                    updater_business_protocol_minor INT NOT NULL,
                    business_package_format_version INT NOT NULL,
                    active_business_release_uid VARCHAR(36) NOT NULL,
                    active_business_release_sequence BIGINT NOT NULL,
                    active_business_version_name VARCHAR(32) NOT NULL,
                    active_business_package_sha256 BINARY(32) NOT NULL,
                    business_process_state VARCHAR(16) NOT NULL,
                    business_process_ready BOOLEAN NOT NULL,
                    negotiated_communication_business_major INT NOT NULL,
                    negotiated_communication_business_minor INT NOT NULL,
                    negotiated_updater_business_major INT NOT NULL,
                    negotiated_updater_business_minor INT NOT NULL,
                    mcu_fixed_frame_revision INT NOT NULL,
                    uart_state VARCHAR(16) NOT NULL,
                    uart_protocol_family VARCHAR(24) NOT NULL,
                    capability_bitmap_hex VARCHAR(16) NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_device_compatibility_projection (
                    asset_id BIGINT PRIMARY KEY,
                    compatibility_status VARCHAR(24) NOT NULL,
                    business_admission_status VARCHAR(16) NOT NULL,
                    latest_software_fact_id BIGINT NOT NULL,
                    lock_version BIGINT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_edge_software_release (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    release_uid VARCHAR(36) NOT NULL UNIQUE,
                    version_name VARCHAR(32) NOT NULL UNIQUE,
                    release_sequence BIGINT NOT NULL UNIQUE,
                    package_sha256 BINARY(32) NOT NULL UNIQUE,
                    package_format_version INT NOT NULL,
                    backend_command_contract_version INT NOT NULL,
                    device_event_contract_version INT NOT NULL,
                    communication_business_protocol_major INT NOT NULL,
                    communication_business_protocol_minor INT NOT NULL,
                    updater_business_protocol_major INT NOT NULL,
                    updater_business_protocol_minor INT NOT NULL,
                    uart_protocol_family VARCHAR(24) NOT NULL,
                    uart_protocol_major INT,
                    uart_protocol_minor INT,
                    required_fixed_frame_revision INT,
                    required_mcu_capability_bitmap_hex VARCHAR(16) NOT NULL,
                    provided_business_capability_bitmap_hex VARCHAR(16) NOT NULL,
                    declaration_sha256 BINARY(32) NOT NULL UNIQUE,
                    created_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_edge_software_release_sequence (
                    singleton_id INT PRIMARY KEY,
                    last_release_sequence BIGINT NOT NULL,
                    lock_version BIGINT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_edge_software_release_control (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    release_uid VARCHAR(36) NOT NULL UNIQUE,
                    create_operation_uid VARCHAR(36) NOT NULL UNIQUE,
                    version_name VARCHAR(32) NOT NULL UNIQUE,
                    release_sequence BIGINT NOT NULL UNIQUE,
                    release_status VARCHAR(24) NOT NULL,
                    verification_operation_uid VARCHAR(36),
                    package_object_key VARCHAR(512) NOT NULL,
                    signature_object_key VARCHAR(512) NOT NULL,
                    package_sha256 BINARY(32),
                    package_size BIGINT,
                    signature_sha256 BINARY(32),
                    signature_bytes BINARY(64),
                    signing_key_id VARCHAR(64),
                    declaration_id BIGINT UNIQUE,
                    verification_error_code VARCHAR(64),
                    verification_error_message VARCHAR(500),
                    release_notes VARCHAR(1000),
                    created_by_platform_admin_id BIGINT NOT NULL,
                    verified_by_platform_admin_id BIGINT,
                    verified_at TIMESTAMP,
                    approved_by_platform_admin_id BIGINT,
                    approved_at TIMESTAMP,
                    suspended_by_platform_admin_id BIGINT,
                    suspended_at TIMESTAMP,
                    suspension_reason VARCHAR(500),
                    retired_by_platform_admin_id BIGINT,
                    retired_at TIMESTAMP,
                    retirement_reason VARCHAR(500),
                    artifact_uploaded_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    lock_version BIGINT NOT NULL DEFAULT 0
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_edge_software_release_action (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    operation_uid VARCHAR(36) NOT NULL UNIQUE,
                    release_control_id BIGINT NOT NULL,
                    action_type VARCHAR(32) NOT NULL,
                    requested_by_platform_admin_id BIGINT NOT NULL,
                    reason VARCHAR(500) NOT NULL,
                    resulting_status VARCHAR(24) NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_edge_software_rollout (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    rollout_uid VARCHAR(36) NOT NULL UNIQUE,
                    create_operation_uid VARCHAR(36) NOT NULL UNIQUE,
                    release_control_id BIGINT NOT NULL,
                    rollout_status VARCHAR(24) NOT NULL,
                    validation_asset_id BIGINT NOT NULL,
                    batch_size INT NOT NULL,
                    maximum_wave_no INT NOT NULL,
                    current_wave_no INT NOT NULL,
                    observation_window_seconds INT NOT NULL,
                    download_timeout_seconds INT NOT NULL,
                    drain_timeout_seconds INT NOT NULL,
                    maximum_retry_count INT NOT NULL,
                    remote_dispatch_enabled_snapshot BOOLEAN NOT NULL,
                    change_reason VARCHAR(500) NOT NULL,
                    created_by_platform_admin_id BIGINT NOT NULL,
                    stopped_by_platform_admin_id BIGINT,
                    stopped_at TIMESTAMP,
                    stop_reason VARCHAR(500),
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    lock_version BIGINT NOT NULL DEFAULT 0
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_edge_software_deployment (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    deployment_uid VARCHAR(36) NOT NULL UNIQUE,
                    rollout_id BIGINT NOT NULL,
                    release_id BIGINT NOT NULL,
                    asset_id BIGINT NOT NULL,
                    tenant_id BIGINT,
                    organization_id BIGINT,
                    deployment_kind VARCHAR(16) NOT NULL,
                    wave_no INT NOT NULL,
                    deployment_status VARCHAR(40) NOT NULL,
                    eligibility_status VARCHAR(16) NOT NULL,
                    eligibility_snapshot CLOB NOT NULL,
                    eligibility_sha256 BINARY(32) NOT NULL,
                    source_software_fact_id BIGINT NOT NULL,
                    source_management_state_sequence BIGINT NOT NULL,
                    source_business_release_uid VARCHAR(36) NOT NULL,
                    source_business_release_sequence BIGINT NOT NULL,
                    command_uid VARCHAR(36),
                    reliable_task_uid VARCHAR(36),
                    edge_update_uid VARCHAR(36),
                    control_sequence BIGINT,
                    cancel_command_uid VARCHAR(36),
                    cancel_reliable_task_uid VARCHAR(36),
                    cancel_control_sequence BIGINT,
                    cancellation_status VARCHAR(16) NOT NULL DEFAULT 'NONE',
                    cancel_reason VARCHAR(500),
                    cancel_requested_by_platform_admin_id BIGINT,
                    cancel_requested_at TIMESTAMP,
                    cancel_result_at TIMESTAMP,
                    stage_sequence BIGINT NOT NULL DEFAULT 0,
                    business_admission_state VARCHAR(16) NOT NULL DEFAULT 'OPEN',
                    download_attempt_count INT NOT NULL DEFAULT 0,
                    target_attempt_count INT NOT NULL DEFAULT 0,
                    rollback_attempt_count INT NOT NULL DEFAULT 0,
                    installed_release_uid VARCHAR(36),
                    installed_version_name VARCHAR(32),
                    installed_release_sequence BIGINT,
                    installed_package_sha256 BINARY(32),
                    database_restored BOOLEAN NOT NULL DEFAULT FALSE,
                    error_code VARCHAR(64),
                    last_event_uid VARCHAR(36),
                    queued_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    lock_version BIGINT NOT NULL DEFAULT 0
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_edge_software_rollout_action (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    operation_uid VARCHAR(36) NOT NULL UNIQUE,
                    rollout_id BIGINT NOT NULL,
                    action_type VARCHAR(16) NOT NULL,
                    requested_by_platform_admin_id BIGINT NOT NULL,
                    reason VARCHAR(500) NOT NULL,
                    resulting_status VARCHAR(24) NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_edge_software_deployment_progress (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    event_uid VARCHAR(36) NOT NULL UNIQUE,
                    source_inbox_id BIGINT NOT NULL UNIQUE,
                    deployment_id BIGINT NOT NULL,
                    edge_update_uid VARCHAR(36) NOT NULL,
                    stage VARCHAR(40) NOT NULL,
                    stage_sequence BIGINT NOT NULL,
                    business_admission_state VARCHAR(16) NOT NULL,
                    download_attempt_count INT NOT NULL,
                    target_attempt_count INT NOT NULL,
                    rollback_attempt_count INT NOT NULL,
                    installed_release_uid VARCHAR(36),
                    installed_version_name VARCHAR(32),
                    installed_release_sequence BIGINT,
                    installed_package_sha256 BINARY(32),
                    database_restored BOOLEAN NOT NULL,
                    error_code VARCHAR(64),
                    payload_sha256 BINARY(32) NOT NULL,
                    normalized_payload CLOB NOT NULL,
                    occurred_at TIMESTAMP,
                    received_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    UNIQUE (deployment_id, stage_sequence)
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_edge_software_deployment_cancel_result (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    event_uid VARCHAR(36) NOT NULL UNIQUE,
                    source_inbox_id BIGINT NOT NULL UNIQUE,
                    deployment_id BIGINT NOT NULL,
                    cancel_command_uid VARCHAR(36) NOT NULL UNIQUE,
                    edge_update_uid VARCHAR(36) NOT NULL,
                    control_sequence BIGINT NOT NULL,
                    result VARCHAR(16) NOT NULL,
                    observed_stage VARCHAR(40) NOT NULL,
                    business_admission_state VARCHAR(16) NOT NULL,
                    error_code VARCHAR(64),
                    payload_sha256 BINARY(32) NOT NULL,
                    normalized_payload CLOB NOT NULL,
                    occurred_at TIMESTAMP,
                    received_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_mcu_firmware_rollout (
                    id BIGINT PRIMARY KEY,
                    rollout_status VARCHAR(24) NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_mcu_firmware_deployment (
                    id BIGINT PRIMARY KEY,
                    rollout_id BIGINT NOT NULL,
                    asset_id BIGINT NOT NULL,
                    deployment_status VARCHAR(32) NOT NULL
                )
                """);
    }

    private void seedDevices() {
        LocalDateTime now = LocalDateTime.now(ZoneOffset.UTC);
        jdbc.update(
                "INSERT INTO iam_platform_admin (id, display_name) VALUES (9, ?)",
                "发布管理员");
        jdbc.update("""
                INSERT INTO dev_edge_software_release_sequence (
                    singleton_id, last_release_sequence, lock_version,
                    created_at, updated_at
                ) VALUES (1, 1, 0, ?, ?)
                """, now, now);
        for (int index = 0; index < 2; index++) {
            long assetId = index + 1L;
            String hardwareSn = index == 0 ? VALIDATION_SN : WAVE_SN;
            jdbc.update("""
                    INSERT INTO dev_device_asset (
                        id, hardware_sn, tenant_id, organization_id,
                        lifecycle_status, acceptance_status
                    ) VALUES (?, ?, NULL, NULL, 'NORMAL', 'PASSED')
                    """, assetId, hardwareSn);
            jdbc.update("""
                    INSERT INTO dev_device_management_profile (
                        asset_id, architecture_generation
                    ) VALUES (?, 'PERMANENT_V1')
                    """, assetId);
            jdbc.update("""
                    INSERT INTO dev_device_software_fact (
                        asset_id, management_state_sequence,
                        business_gate_state,
                        communication_business_protocol_major,
                        communication_business_protocol_minor,
                        updater_business_protocol_major,
                        updater_business_protocol_minor,
                        business_package_format_version,
                        active_business_release_uid,
                        active_business_release_sequence,
                        active_business_version_name,
                        active_business_package_sha256,
                        business_process_state, business_process_ready,
                        negotiated_communication_business_major,
                        negotiated_communication_business_minor,
                        negotiated_updater_business_major,
                        negotiated_updater_business_minor,
                        mcu_fixed_frame_revision, uart_state,
                        uart_protocol_family, capability_bitmap_hex
                    ) VALUES (
                        ?, ?, 'OPEN', 1, 0, 1, 0, 1, ?, 1,
                        '1.0.0', ?,
                        'RUNNING', TRUE, 1, 0, 1, 0,
                        2, 'READY', 'FIXED_FRAME', '0000000000000000'
                    )
                    """,
                    assetId,
                    assetId,
                    CURRENT_RELEASE_UID.toString(),
                    HexFormat.of().parseHex(CURRENT_PACKAGE_SHA256));
            Long factId = jdbc.queryForObject(
                    "SELECT id FROM dev_device_software_fact WHERE asset_id = ?",
                    Long.class,
                    assetId);
            jdbc.update("""
                    INSERT INTO dev_device_compatibility_projection (
                        asset_id, compatibility_status,
                        business_admission_status, latest_software_fact_id
                    ) VALUES (?, 'FULLY_COMPATIBLE', 'ACCEPTING', ?)
                    """, assetId, factId);
        }
    }

    private static String sha256(byte[] value) throws Exception {
        return HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(value));
    }

    private static final class MemoryArtifactStorage
            implements BusinessReleaseArtifactStoragePort {

        private final Map<String, byte[]> values = new HashMap<>();

        @Override
        public Readiness readiness() {
            return new Readiness(true, "测试私有制品存储可用");
        }

        @Override
        public void storeImmutable(
                String objectKey, Path source, String sha256, long size) {
            try {
                byte[] content = Files.readAllBytes(source);
                byte[] existing = values.putIfAbsent(objectKey, content);
                if (existing != null && !Arrays.equals(existing, content)) {
                    throw new IllegalStateException("immutable conflict");
                }
            } catch (java.io.IOException exception) {
                throw new IllegalStateException(exception);
            }
        }

        @Override
        public void download(String objectKey, Path target) {
            try {
                Files.write(target, values.get(objectKey));
            } catch (java.io.IOException exception) {
                throw new IllegalStateException(exception);
            }
        }
    }

    private static final class DatabaseClockJdbcTemplate extends JdbcTemplate {

        private DatabaseClockJdbcTemplate(DataSource dataSource) {
            super(dataSource);
        }

        @Override
        public <T> T queryForObject(String sql, Class<T> requiredType) {
            if ("SELECT UTC_TIMESTAMP(3)".equals(sql)) {
                return requiredType.cast(LocalDateTime.now(ZoneOffset.UTC));
            }
            return super.queryForObject(sql, requiredType);
        }
    }

    private record InstalledIdentity(
            UUID releaseUid,
            String versionName,
            long releaseSequence,
            String packageSha256) {
    }

    private record RemoteTestContext(
            JsonMapper objectMapper,
            List<ReliablePlatformDeviceControlTaskRegistration> registered,
            ReliableDeviceTaskProofPort taskProof) {
    }
}
