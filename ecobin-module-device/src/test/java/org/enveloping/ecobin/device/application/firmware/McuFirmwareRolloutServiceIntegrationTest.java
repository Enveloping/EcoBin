package org.enveloping.ecobin.device.application.firmware;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedPlatformDeviceAssetFactEvent;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.CreateRolloutRequest;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.DeploymentView;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.RegisterReleaseRequest;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.RolloutActionRequest;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.RolloutView;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRef;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWake;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWakePort;
import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRef;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.DeviceScopePersistenceRef;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeviceScope;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;
import tools.jackson.databind.json.JsonMapper;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import javax.sql.DataSource;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class McuFirmwareRolloutServiceIntegrationTest {

    private static final UUID RELEASE_UID = UUID.fromString(
            "8c000000-0000-4000-8000-000000000001");
    private static final String PACKAGE_SHA256 = "a".repeat(64);
    private static final String VERSION = "2.1.0";
    private static final long VERSION_CODE = 20100;
    private static final String IDENTITY = "0123456789abcdef";
    private static final String HARDWARE_COMPATIBILITY =
            "ECOBIN_MAINBOARD_V1.1";
    private static final String VALIDATION_SN = "SN-FIRMWARE-VALIDATION-01";
    private static final String WAVE_ONE_SN = "SN-FIRMWARE-WAVE-000001";
    private static final String WAVE_TWO_SN = "SN-FIRMWARE-WAVE-000002";

    private JdbcTemplate jdbc;
    private DataSource dataSource;
    private JsonMapper objectMapper;
    private McuFirmwareRolloutService service;
    private List<ReliablePlatformDeviceControlTaskRegistration> tasks;
    private Map<UUID, UUID> updateUids;
    private ReliableDeviceTaskProofPort taskProof;
    private ReliableTaskWakePort taskWake;
    private long nextInboxId;

    @BeforeEach
    void setUp() {
        DriverManagerDataSource configuredDataSource =
                new DriverManagerDataSource();
        configuredDataSource.setDriverClassName("org.h2.Driver");
        configuredDataSource.setUrl("jdbc:h2:mem:mcu_firmware_"
                + UUID.randomUUID()
                + ";MODE=MySQL;DB_CLOSE_DELAY=-1");
        dataSource = configuredDataSource;
        jdbc = new DatabaseClockJdbcTemplate(dataSource);
        objectMapper = JsonMapper.builder().build();
        createSchema();
        seedAssets();

        DeviceScopeAuthorizationPort authorization =
                mock(DeviceScopeAuthorizationPort.class);
        when(authorization.authorize(any())).thenAnswer(ignored -> actor());
        PlatformDeviceAssetTaskRefFactory taskRefs =
                mock(PlatformDeviceAssetTaskRefFactory.class);
        when(taskRefs.issue(anyLong())).thenReturn(
                mock(PlatformDeviceAssetTaskRef.class));
        ReliablePlatformDeviceControlTaskRegistrationPort registrations =
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class);
        tasks = new ArrayList<>();
        updateUids = new HashMap<>();
        when(registrations.register(any())).thenAnswer(invocation -> {
            tasks.add(invocation.getArgument(0));
            return UUID.randomUUID();
        });
        taskProof = mock(ReliableDeviceTaskProofPort.class);
        taskWake = mock(ReliableTaskWakePort.class);
        service = new McuFirmwareRolloutService(
                jdbc,
                objectMapper,
                authorization,
                new DeviceConfigurationCanonicalizer(),
                taskRefs,
                registrations,
                taskProof,
                taskWake);
        nextInboxId = 100;
    }

    @Test
    void validationPromotionAndWavesAreExplicitAndFailClosed() {
        registerRelease(UUID.randomUUID(), "initial release");
        UUID rolloutUid = UUID.randomUUID();
        RolloutView draft = service.createRollout(
                rolloutUid,
                new CreateRolloutRequest(
                        RELEASE_UID,
                        VALIDATION_SN,
                        List.of(WAVE_ONE_SN, WAVE_TWO_SN),
                        1,
                        "controlled field rollout"));
        assertEquals("DRAFT", draft.status());
        assertEquals(2, draft.maximumWaveNo());
        assertEquals(3, draft.deployments().size());

        RolloutView validating = service.startValidation(
                UUID.randomUUID(),
                rolloutUid,
                new RolloutActionRequest("validate one known device"));
        assertEquals("VALIDATING", validating.status());
        assertEquals(1, tasks.size());
        JsonNode frozenEnvelope = objectMapper.readTree(
                tasks.getFirst().executionEnvelope());
        assertTrue(frozenEnvelope.path("cosGrant").isNull());
        assertEquals(VALIDATION_SN,
                frozenEnvelope.path("targetDeviceName").asText());

        DeploymentView validation = deployment(
                validating, "VALIDATION", 0);
        assertEquals(
                TrustedDeviceEventApplyResult.APPLIED,
                service.applyProgress(progress(
                        validation,
                        "SUCCEEDED",
                        1,
                        0,
                        VERSION,
                        VERSION_CODE,
                        IDENTITY)));
        RolloutView awaitingPromotion = service.detail(rolloutUid);
        assertEquals("AWAITING_PROMOTION", awaitingPromotion.status());
        assertEquals(20100L, jdbc.queryForObject(
                "SELECT mcu_firmware_version_code FROM dev_device_asset"
                        + " WHERE hardware_sn = ?",
                Long.class,
                VALIDATION_SN));

        RolloutView active = service.promote(
                UUID.randomUUID(),
                rolloutUid,
                new RolloutActionRequest("validation evidence reviewed"));
        assertEquals("ACTIVE", active.status());
        assertEquals("PROMOTED", active.release().status());

        RolloutView waveOne = service.advanceWave(
                UUID.randomUUID(),
                rolloutUid,
                new RolloutActionRequest("dispatch wave one"));
        assertEquals(1, waveOne.currentWaveNo());
        assertEquals(2, tasks.size());
        TargetApiException running = assertThrows(
                TargetApiException.class,
                () -> service.advanceWave(
                        UUID.randomUUID(),
                        rolloutUid,
                        new RolloutActionRequest("must not skip wave one")));
        assertEquals("DEVICE.MCU_FIRMWARE_WAVE_RUNNING", running.code());

        DeploymentView first = deployment(waveOne, "WAVE", 1);
        service.applyProgress(progress(
                first,
                "PREPARED",
                0,
                0,
                null,
                null,
                null));
        service.applyProgress(progress(
                first,
                "PREFLIGHT",
                0,
                0,
                null,
                null,
                null));
        assertEquals("PREPARED", deployment(
                service.detail(rolloutUid), "WAVE", 1).status());
        service.applyProgress(progress(
                first,
                "SUCCEEDED",
                1,
                0,
                VERSION,
                VERSION_CODE,
                IDENTITY));

        RolloutView waveTwo = service.advanceWave(
                UUID.randomUUID(),
                rolloutUid,
                new RolloutActionRequest("dispatch wave two"));
        assertEquals(2, waveTwo.currentWaveNo());
        DeploymentView second = deployment(waveTwo, "WAVE", 2);
        service.applyProgress(progress(
                second,
                "ROLLED_BACK",
                1,
                1,
                "1.9.0",
                10900L,
                "fedcba9876543210"));
        TargetApiException failedWave = assertThrows(
                TargetApiException.class,
                () -> service.advanceWave(
                        UUID.randomUUID(),
                        rolloutUid,
                        new RolloutActionRequest("must not skip a rollback")));
        assertEquals("DEVICE.MCU_FIRMWARE_WAVE_FAILED", failedWave.code());
        assertEquals("1.9.0", jdbc.queryForObject(
                "SELECT mcu_firmware_version FROM dev_device_runtime_state"
                        + " WHERE asset_id = 3",
                String.class));
    }

    @Test
    void idempotencyReplayMustMatchTheEntireOriginalRequest() {
        UUID releaseOperation = UUID.randomUUID();
        registerRelease(releaseOperation, "original notes");
        TargetApiException changedRelease = assertThrows(
                TargetApiException.class,
                () -> registerRelease(releaseOperation, "changed notes"));
        assertEquals("DEVICE.IDEMPOTENCY_CONFLICT", changedRelease.code());

        UUID rolloutOperation = UUID.randomUUID();
        CreateRolloutRequest original = new CreateRolloutRequest(
                RELEASE_UID,
                VALIDATION_SN,
                List.of(WAVE_ONE_SN, WAVE_TWO_SN),
                1,
                "original rollout");
        service.createRollout(rolloutOperation, original);
        assertEquals("DRAFT",
                service.createRollout(rolloutOperation, original).status());
        TargetApiException changedTargets = assertThrows(
                TargetApiException.class,
                () -> service.createRollout(
                        rolloutOperation,
                        new CreateRolloutRequest(
                                RELEASE_UID,
                                VALIDATION_SN,
                                List.of(WAVE_ONE_SN),
                                1,
                                "original rollout")));
        assertEquals("DEVICE.IDEMPOTENCY_CONFLICT", changedTargets.code());

        UUID actionOperation = UUID.randomUUID();
        service.startValidation(
                actionOperation,
                rolloutOperation,
                new RolloutActionRequest("original action reason"));
        TargetApiException changedAction = assertThrows(
                TargetApiException.class,
                () -> service.startValidation(
                        actionOperation,
                        rolloutOperation,
                        new RolloutActionRequest("changed action reason")));
        assertEquals("DEVICE.IDEMPOTENCY_CONFLICT", changedAction.code());
    }

    @Test
    void packageAcquisitionFailureIsVisibleAndMayAdvanceAfterRetry() {
        registerRelease(UUID.randomUUID(), "retryable package acquisition");
        UUID rolloutUid = UUID.randomUUID();
        service.createRollout(
                rolloutUid,
                rolloutRequest(RELEASE_UID, "download retry test"));
        RolloutView validating = service.startValidation(
                UUID.randomUUID(),
                rolloutUid,
                new RolloutActionRequest("start validation"));
        DeploymentView deployment = deployment(validating, "VALIDATION", 0);

        service.applyProgress(progress(
                deployment,
                "PACKAGE_FETCH_FAILED",
                0,
                0,
                null,
                null,
                null));
        DeploymentView failedAcquisition = deployment(
                service.detail(rolloutUid), "VALIDATION", 0);
        assertEquals(
                "PACKAGE_FETCH_FAILED",
                failedAcquisition.status());
        assertEquals("COS_DOWNLOAD_FAILED", failedAcquisition.errorCode());
        verify(taskWake).wake(any(ReliableTaskWake.class));

        service.applyProgress(progress(
                deployment,
                "PREFLIGHT",
                0,
                0,
                null,
                null,
                null));
        DeploymentView resumed = deployment(
                service.detail(rolloutUid), "VALIDATION", 0);
        assertEquals("PREFLIGHT", resumed.status());
        assertEquals(null, resumed.errorCode());
        verify(taskWake, times(1)).wake(any(ReliableTaskWake.class));
    }

    @Test
    void revisionOneDeviceCannotEnterAutomaticFirmwareRollout() {
        registerRelease(UUID.randomUUID(), "revision two only");
        jdbc.update("""
                        UPDATE dev_device_asset
                        SET mcu_fixed_frame_revision = 1
                        WHERE hardware_sn = ?
                        """,
                VALIDATION_SN);

        TargetApiException rejected = assertThrows(
                TargetApiException.class,
                () -> service.createRollout(
                        UUID.randomUUID(),
                        new CreateRolloutRequest(
                                RELEASE_UID,
                                VALIDATION_SN,
                                List.of(WAVE_ONE_SN),
                                1,
                                "must reject revision one")));

        assertEquals(
                "DEVICE.MCU_FIRMWARE_PROTOCOL_REVISION_UNSUPPORTED",
                rejected.code());
        assertEquals(0L, jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_mcu_firmware_rollout",
                Long.class));
    }

    @Test
    void concurrentRolloutsCannotReserveTheSamePhysicalAssets()
            throws Exception {
        registerRelease(UUID.randomUUID(), "first release");
        UUID secondReleaseUid = UUID.randomUUID();
        String secondPackageSha256 = "d".repeat(64);
        service.registerRelease(
                UUID.randomUUID(),
                new RegisterReleaseRequest(
                        secondReleaseUid,
                        "2.2.0",
                        20200L,
                        "fedcba9876543210",
                        HARDWARE_COMPATIBILITY,
                        2,
                        "ecobin/mcu-firmware/" + secondReleaseUid + "/"
                                + secondPackageSha256 + ".efw",
                        secondPackageSha256,
                        8192L,
                        "second release"));

        TransactionTemplate transaction = new TransactionTemplate(
                new DataSourceTransactionManager(dataSource));
        CountDownLatch firstCreated = new CountDownLatch(1);
        CountDownLatch releaseFirst = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<RolloutView> first = executor.submit(() ->
                    transaction.execute(status -> {
                        RolloutView created = service.createRollout(
                                UUID.randomUUID(),
                                rolloutRequest(RELEASE_UID, "first rollout"));
                        firstCreated.countDown();
                        await(releaseFirst);
                        return created;
                    }));
            assertTrue(firstCreated.await(5, TimeUnit.SECONDS));

            Future<Object> second = executor.submit(() -> {
                try {
                    return transaction.execute(status ->
                            service.createRollout(
                                    UUID.randomUUID(),
                                    rolloutRequest(
                                            secondReleaseUid,
                                            "competing rollout")));
                } catch (TargetApiException exception) {
                    return exception;
                }
            });

            assertThrows(
                    TimeoutException.class,
                    () -> second.get(250, TimeUnit.MILLISECONDS));
            releaseFirst.countDown();

            assertEquals("DRAFT", first.get(5, TimeUnit.SECONDS).status());
            Object competing = second.get(5, TimeUnit.SECONDS);
            assertTrue(competing instanceof TargetApiException);
            assertEquals(
                    "DEVICE.MCU_FIRMWARE_DEVICE_BUSY",
                    ((TargetApiException) competing).code());
            assertEquals(1L, jdbc.queryForObject(
                    "SELECT COUNT(*) FROM dev_mcu_firmware_rollout",
                    Long.class));
        } finally {
            releaseFirst.countDown();
            executor.shutdownNow();
        }
    }

    private static CreateRolloutRequest rolloutRequest(
            UUID releaseUid,
            String reason) {
        return new CreateRolloutRequest(
                releaseUid,
                VALIDATION_SN,
                List.of(WAVE_ONE_SN, WAVE_TWO_SN),
                1,
                reason);
    }

    private static void await(CountDownLatch latch) {
        try {
            if (!latch.await(5, TimeUnit.SECONDS)) {
                throw new IllegalStateException("timed out waiting for test latch");
            }
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("interrupted while waiting", exception);
        }
    }

    private void registerRelease(UUID operationUid, String notes) {
        service.registerRelease(
                operationUid,
                new RegisterReleaseRequest(
                        RELEASE_UID,
                        VERSION,
                        VERSION_CODE,
                        IDENTITY,
                        HARDWARE_COMPATIBILITY,
                        2,
                        "ecobin/mcu-firmware/" + RELEASE_UID + "/"
                                + PACKAGE_SHA256 + ".efw",
                        PACKAGE_SHA256,
                        8192L,
                        notes));
    }

    private TrustedPlatformDeviceAssetFactEvent progress(
            DeploymentView deployment,
            String stage,
            int targetAttempts,
            int rollbackAttempts,
            String installedVersion,
            Long installedVersionCode,
            String installedIdentity) {
        ObjectNode normalized = objectMapper.createObjectNode();
        normalized.put("eventCanonicalSha256", "b".repeat(64));
        normalized.putObject("trustedSource")
                .put("deviceName", deployment.hardwareSn());
        ObjectNode event = normalized.putObject("event");
        event.put("eventUid", UUID.randomUUID().toString());
        event.put("eventType", McuFirmwareRolloutService.EVENT_TYPE);
        event.put("commandUid", deployment.commandUid().toString());
        event.put("occurredAt", Instant.now().toString());
        event.put("payloadSha256", "c".repeat(64));
        event.putObject("target")
                .put("type", "MCU_FIRMWARE_DEPLOYMENT")
                .put("uid", deployment.deploymentUid().toString());
        ObjectNode payload = event.putObject("payload");
        payload.put("deploymentUid", deployment.deploymentUid().toString());
        payload.put("releaseUid", RELEASE_UID.toString());
        payload.put("firmwareVersion", VERSION);
        payload.put("firmwareVersionCode", VERSION_CODE);
        payload.put("firmwareIdentityHex", IDENTITY);
        payload.put("fixedFrameRevision", 2);
        payload.put("source", "CLOUD");
        payload.put("legacyPreflight", false);
        payload.put("downgradeAuthorized", false);
        payload.put("stage", stage);
        payload.put("targetAttemptCount", targetAttempts);
        payload.put("rollbackAttemptCount", rollbackAttempts);
        if ("PACKAGE_FETCH_FAILED".equals(stage)) {
            payload.put("errorCode", "COS_DOWNLOAD_FAILED");
        } else {
            payload.putNull("errorCode");
        }
        payload.put("updateUid", stableUpdateUid(deployment).toString());
        if (installedVersion == null) {
            payload.putNull("installedFirmwareVersion");
            payload.putNull("installedFirmwareVersionCode");
            payload.putNull("installedFirmwareIdentityHex");
        } else {
            payload.put("installedFirmwareVersion", installedVersion);
            payload.put("installedFirmwareVersionCode", installedVersionCode);
            payload.put("installedFirmwareIdentityHex", installedIdentity);
        }
        TrustedPlatformInboxRef sourceInbox =
                mock(TrustedPlatformInboxRef.class);
        long inboxId = nextInboxId++;
        when(sourceInbox.use(any())).thenAnswer(invocation -> {
            TrustedPlatformInboxRef.PlatformInboxFunction<Object> function =
                    invocation.getArgument(0);
            return function.apply(inboxId);
        });
        return new TrustedPlatformDeviceAssetFactEvent(
                sourceInbox,
                McuFirmwareRolloutService.EVENT_TYPE,
                2,
                objectMapper.writeValueAsString(normalized));
    }

    private UUID stableUpdateUid(DeploymentView deployment) {
        return updateUids.computeIfAbsent(
                deployment.deploymentUid(),
                ignored -> UUID.randomUUID());
    }

    private static DeploymentView deployment(
            RolloutView rollout,
            String kind,
            int waveNo) {
        return rollout.deployments().stream()
                .filter(item -> kind.equals(item.kind())
                        && item.waveNo() == waveNo)
                .findFirst()
                .orElseThrow();
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
                "Firmware operator",
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
                    acceptance_status VARCHAR(16) NOT NULL,
                    mcu_firmware_version_code BIGINT,
                    mcu_firmware_identity_hex VARCHAR(16),
                    mcu_fixed_frame_revision INT,
                    updated_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_device_transport_state (
                    asset_id BIGINT PRIMARY KEY,
                    onenet_connection_status VARCHAR(16) NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_device_runtime_state (
                    asset_id BIGINT PRIMARY KEY,
                    mcu_firmware_version VARCHAR(32),
                    lock_version BIGINT NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_mcu_firmware_release (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    release_uid VARCHAR(36) NOT NULL UNIQUE,
                    operation_uid VARCHAR(36) NOT NULL UNIQUE,
                    firmware_version VARCHAR(32) NOT NULL,
                    firmware_version_code BIGINT NOT NULL,
                    firmware_identity_hex VARCHAR(16) NOT NULL,
                    hardware_compatibility VARCHAR(64) NOT NULL,
                    fixed_frame_revision INT NOT NULL,
                    package_object_key VARCHAR(512) NOT NULL,
                    package_sha256 BINARY(32) NOT NULL,
                    package_size BIGINT NOT NULL,
                    release_status VARCHAR(16) NOT NULL,
                    release_notes VARCHAR(1000),
                    created_by_platform_admin_id BIGINT NOT NULL,
                    promoted_by_platform_admin_id BIGINT,
                    promoted_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_mcu_firmware_rollout (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    rollout_uid VARCHAR(36) NOT NULL UNIQUE,
                    operation_uid VARCHAR(36) NOT NULL UNIQUE,
                    release_id BIGINT NOT NULL,
                    rollout_status VARCHAR(24) NOT NULL,
                    batch_size INT NOT NULL,
                    maximum_wave_no INT NOT NULL,
                    current_wave_no INT NOT NULL,
                    validation_asset_id BIGINT NOT NULL,
                    change_reason VARCHAR(500) NOT NULL,
                    created_by_platform_admin_id BIGINT NOT NULL,
                    promoted_by_platform_admin_id BIGINT,
                    promoted_at TIMESTAMP,
                    stopped_by_platform_admin_id BIGINT,
                    stopped_at TIMESTAMP,
                    stop_reason VARCHAR(500),
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    lock_version BIGINT NOT NULL DEFAULT 0
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_mcu_firmware_deployment (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    deployment_uid VARCHAR(36) NOT NULL UNIQUE,
                    rollout_id BIGINT NOT NULL,
                    release_id BIGINT NOT NULL,
                    asset_id BIGINT NOT NULL,
                    tenant_id BIGINT,
                    organization_id BIGINT,
                    deployment_kind VARCHAR(16) NOT NULL,
                    wave_no INT NOT NULL,
                    deployment_status VARCHAR(32) NOT NULL,
                    command_uid VARCHAR(36),
                    reliable_task_uid VARCHAR(36),
                    edge_update_uid VARCHAR(36),
                    target_attempt_count INT NOT NULL DEFAULT 0,
                    rollback_attempt_count INT NOT NULL DEFAULT 0,
                    installed_firmware_version VARCHAR(32),
                    installed_firmware_version_code BIGINT,
                    installed_firmware_identity_hex VARCHAR(16),
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
                CREATE TABLE dev_mcu_firmware_progress (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    event_uid VARCHAR(36) NOT NULL UNIQUE,
                    source_inbox_id BIGINT NOT NULL UNIQUE,
                    deployment_id BIGINT NOT NULL,
                    edge_update_uid VARCHAR(36) NOT NULL,
                    stage VARCHAR(32) NOT NULL,
                    target_attempt_count INT NOT NULL,
                    rollback_attempt_count INT NOT NULL,
                    error_code VARCHAR(64),
                    payload_sha256 BINARY(32) NOT NULL,
                    normalized_payload CLOB NOT NULL,
                    occurred_at TIMESTAMP,
                    received_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_mcu_firmware_rollout_action (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    operation_uid VARCHAR(36) NOT NULL UNIQUE,
                    rollout_id BIGINT NOT NULL,
                    action_type VARCHAR(24) NOT NULL,
                    resulting_wave_no INT,
                    requested_by_platform_admin_id BIGINT NOT NULL,
                    reason VARCHAR(500) NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """);
    }

    private void seedAssets() {
        LocalDateTime now = LocalDateTime.now(ZoneOffset.UTC);
        jdbc.update(
                "INSERT INTO iam_platform_admin (id, display_name) VALUES (9, ?)",
                "Firmware operator");
        List<String> hardwareSns = List.of(
                VALIDATION_SN, WAVE_ONE_SN, WAVE_TWO_SN);
        for (int index = 0; index < hardwareSns.size(); index++) {
            long assetId = index + 1L;
            jdbc.update("""
                            INSERT INTO dev_device_asset (
                                id, hardware_sn, tenant_id, organization_id,
                                lifecycle_status, acceptance_status,
                                mcu_fixed_frame_revision,
                                updated_at
                            ) VALUES (
                                ?, ?, NULL, NULL, 'NORMAL', 'PASSED', 2, ?
                            )
                            """,
                    assetId,
                    hardwareSns.get(index),
                    now);
            jdbc.update("""
                            INSERT INTO dev_device_transport_state (
                                asset_id, onenet_connection_status
                            ) VALUES (?, 'ONLINE')
                            """,
                    assetId);
            jdbc.update("""
                            INSERT INTO dev_device_runtime_state (
                                asset_id, mcu_firmware_version,
                                lock_version, updated_at
                            ) VALUES (?, NULL, 0, ?)
                            """,
                    assetId,
                    now);
        }
    }

    private static final class DatabaseClockJdbcTemplate
            extends JdbcTemplate {

        private DatabaseClockJdbcTemplate(
                javax.sql.DataSource dataSource) {
            super(dataSource);
        }

        @Override
        public <T> T queryForObject(String sql, Class<T> requiredType) {
            if ("SELECT UTC_TIMESTAMP(3)".equals(sql)) {
                return requiredType.cast(
                        LocalDateTime.now(ZoneOffset.UTC));
            }
            return super.queryForObject(sql, requiredType);
        }
    }
}
