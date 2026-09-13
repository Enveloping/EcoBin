package org.enveloping.ecobin;

import jakarta.servlet.http.Cookie;
import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.enveloping.ecobin.device.application.delivery.DeliveryCommandObservationReconciliationItemService;
import org.enveloping.ecobin.device.application.delivery.DeliveryCommandObservationReconciliationScheduler;
import org.enveloping.ecobin.device.application.target.AutomaticDeviceActivationScheduler;
import org.enveloping.ecobin.device.application.target.TargetDeviceApplication;
import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetCanonicalJson;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxExecutionLane;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceiptState;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceInboxWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.recycling.application.bag.Eb1BagCodeService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.dao.DataAccessException;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

/**
 * MySQL proof for the V36 permanent device model.
 *
 * <p>The test deliberately has no deployment, pool, reclaim or manual
 * activation fixture. It proves that the asset is the only lifecycle root,
 * ownership is written once, organization assignment creates the unattended
 * initial configuration, and stopping a device cancels its tasks while
 * keeping scoped historical reads available.</p>
 */
@SpringBootTest(properties = {
        "spring.datasource.url=${ECOBIN_DEVICE_MYSQL_URL}",
        "spring.datasource.username=${ECOBIN_DEVICE_MYSQL_USERNAME}",
        "spring.datasource.password=${ECOBIN_DEVICE_MYSQL_PASSWORD}",
        "spring.sql.init.mode=never",
        "ecobin.database.epoch.test-bypass=false",
        "ecobin.external.mode=fake",
        "ecobin.external.fake.block-inbound=true",
        "ecobin.operations.reliable.workers-enabled=false",
        "ecobin.device.activation.scheduler-enabled=false",
        "ecobin.device.delivery-observation-reconciliation.scheduler-enabled=false",
        "ecobin.miniapp.device-entry-base-url=https://example.test/ecobin/device",
        "ecobin.identity.default-platform-admin.enabled=false",
        "ecobin.funds.wechat-pay.merchant-profile-registration-enabled=false",
        "onenet.subscription.enabled=false",
        "bagCodeKeyK1=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        "jwt.secret=DEVICE_TEST_SECRET_MUST_BE_AT_LEAST_32_BYTES_LONG"
})
@AutoConfigureMockMvc
@Import(TargetDeviceMysqlIntegrationTest.ProbeConfiguration.class)
@EnabledIfEnvironmentVariable(
        named = "ECOBIN_DEVICE_MYSQL_URL",
        matches = "jdbc:mysql:.+")
class TargetDeviceMysqlIntegrationTest {

    private static final String PLATFORM_PASSWORD = "PlatformPass123!";
    private static final String PRINCIPAL_PASSWORD = "PrincipalPass123!";

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private JdbcTemplate jdbc;
    @Autowired
    private PasswordEncoder passwordEncoder;
    @Autowired
    private ObjectMapper objectMapper;
    @Autowired
    private AcceptedSubmissionProbe submissionProbe;
    @Autowired
    private TrustedInboxPort trustedInbox;
    @Autowired
    private ReliableDeviceInboxWorkerPort deviceInboxWorker;
    @Autowired
    private Eb1BagCodeService bagCodeService;
    @Autowired
    private ReliableOperationsJdbcRepository reliableOperationsRepository;

    private String run;
    private String platformLogin;

    @BeforeEach
    void seedPlatformAdministrator() {
        run = Long.toUnsignedString(System.nanoTime(), 36);
        platformLogin = "device-platform-" + run;
        submissionProbe.reset();
        jdbc.update("""
                        INSERT INTO iam_platform_admin (
                            platform_admin_uid, login_name, password_hash,
                            display_name, enabled, failed_login_count,
                            locked_until, auth_version, password_changed_at,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 'Device integration administrator',
                            1, 0, NULL, 0, UTC_TIMESTAMP(3), 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                UUID.randomUUID().toString(),
                platformLogin,
                passwordEncoder.encode(PLATFORM_PASSWORD));
    }

    @Test
    void reconciliationCandidateQueriesAreValidOnMysql() {
        TargetDeviceApplication application =
                mock(TargetDeviceApplication.class);

        new AutomaticDeviceActivationScheduler(jdbc, application)
                .reconcileIncompleteAssets();
        new DeliveryCommandObservationReconciliationScheduler(
                jdbc,
                mock(DeliveryCommandObservationReconciliationItemService.class))
                .reconcileHistoricalDeliveryObservations();
        assertTrue(reliableOperationsRepository
                .lockLegacyBaselineEvidenceWaits(
                        jdbc.queryForObject(
                                "SELECT CURRENT_TIMESTAMP(3)",
                                java.time.LocalDateTime.class))
                .isEmpty());
    }

    @Test
    void factoryProgressReturnsConsistentInitialAssetSnapshot()
            throws Exception {
        BrowserClient platform = new BrowserClient();
        login(platform, "/api/v1/web/platform/auth/sessions",
                platformLogin, PLATFORM_PASSWORD, 201);

        String hardwareSn = "HW-FACTORY-PROGRESS-" + run;
        data(write(
                platform,
                post("/api/v1/web/platform/device-assets"),
                UUID.randomUUID(),
                Map.of(
                        "hardwareSn", hardwareSn,
                        "modelCode", "EC-M0",
                        "productionBatch", "FACTORY-PROGRESS-" + run,
                        "expectedPortCount", 2),
                201));

        MvcResult response = read(platform,
                "/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/factory-progress",
                200);
        JsonNode progress = data(response);
        assertEquals("no-store",
                response.getResponse().getHeader("Cache-Control"));
        assertEquals(2,
                progress.path("factoryBags")
                        .path("expectedPortCount").asInt());
        assertEquals(0,
                progress.path("factoryBags")
                        .path("verifiedCount").asInt());
        assertFalse(progress.path("factoryBags")
                .path("complete").asBoolean());
        assertEquals("PENDING",
                progress.path("acceptance").path("status").asText());
        assertEquals(0,
                progress.path("acceptance").path("generation").asLong());
        assertTrue(progress.path("acceptance")
                .path("currentFailureReasons").isArray());
        assertTrue(progress.path("acceptance")
                .path("authoritativeEvidence").isNull());
        assertTrue(progress.path("acceptance")
                .path("latestEvidence").isNull());
        assertTrue(progress.path("acceptanceRequest")
                .path("taskUid").isNull());
        assertEquals("NOT_ISSUED",
                progress.path("seal").path("status").asText());
        assertEquals("FACTORY_BAGS",
                progress.path("currentStage").asText());
        assertEquals("WAITING_OPERATOR",
                progress.path("status").asText());
        assertEquals("FACTORY_BAGS_INCOMPLETE",
                progress.path("blockingCode").asText());
        assertEquals("SCAN_FACTORY_BAGS",
                progress.path("nextActionCodes").path(0).asText());
        assertTrue(progress.path("fetchedAt").isTextual());
    }

    @Test
    void factoryProgressCountsOnlyCurrentlyVerifiedFactoryBags()
            throws Exception {
        BrowserClient platform = new BrowserClient();
        login(platform, "/api/v1/web/platform/auth/sessions",
                platformLogin, PLATFORM_PASSWORD, 201);

        String hardwareSn = "HW-FACTORY-BAGS-" + run;
        String firstBag = bagCodeService.issue().value();
        String secondBag = bagCodeService.issue().value();
        data(write(
                platform,
                post("/api/v1/web/platform/device-assets"),
                UUID.randomUUID(),
                Map.of(
                        "hardwareSn", hardwareSn,
                        "modelCode", "EC-M0",
                        "productionBatch", "FACTORY-BAGS-" + run,
                        "expectedPortCount", 2),
                201));
        long assetId = assetId(hardwareSn);

        JsonNode placeholders = factoryProgress(platform, hardwareSn);
        assertEquals(0, placeholders.path("factoryBags")
                .path("verifiedCount").asInt());
        assertFalse(placeholders.path("factoryBags")
                .path("complete").asBoolean());

        long platformAdminId = jdbc.queryForObject("""
                        SELECT id FROM iam_platform_admin
                        WHERE login_name = ?
                        """, Long.class, platformLogin);
        String operatorCode = "OP_" + UUID.randomUUID().toString()
                .replace("-", "").substring(0, 12).toUpperCase();
        assertEquals(1, jdbc.update("""
                        INSERT INTO iam_factory_operator (
                            factory_operator_uid, operator_code,
                            display_name, enabled, auth_version,
                            lock_version, created_by_platform_admin_id,
                            created_at, updated_at
                        ) VALUES (?, ?, 'Factory progress operator', 1,
                                  0, 0, ?, UTC_TIMESTAMP(3),
                                  UTC_TIMESTAMP(3))
                        """,
                UUID.randomUUID().toString(),
                operatorCode,
                platformAdminId));
        long operatorId = jdbc.queryForObject("""
                        SELECT id FROM iam_factory_operator
                        WHERE operator_code = ?
                        """, Long.class, operatorCode);

        UUID batchUid = UUID.randomUUID();
        assertEquals(1, jdbc.update("""
                        INSERT INTO rec_bag_label_batch (
                            batch_uid, operation_uid, key_id, label_count,
                            request_sha256, created_by_platform_admin_id,
                            created_at
                        ) VALUES (?, ?, 'K1', 2,
                                  UNHEX(SHA2(?, 256)), ?, UTC_TIMESTAMP(3))
                        """,
                batchUid.toString(),
                UUID.randomUUID().toString(),
                "factory-progress-" + run,
                platformAdminId));
        long batchId = jdbc.queryForObject("""
                        SELECT id FROM rec_bag_label_batch
                        WHERE batch_uid = ?
                        """, Long.class, batchUid.toString());
        assertEquals(1, jdbc.update("""
                        INSERT INTO rec_bag_label_item (
                            batch_id, sequence_no, bag_code, created_at
                        ) VALUES (?, 1, ?, UTC_TIMESTAMP(3))
                        """, batchId, firstBag));
        assertEquals(1, jdbc.update("""
                        INSERT INTO rec_bag_label_item (
                            batch_id, sequence_no, bag_code, created_at
                        ) VALUES (?, 2, ?, UTC_TIMESTAMP(3))
                        """, batchId, secondBag));

        long firstLabelId = jdbc.queryForObject("""
                        SELECT id FROM rec_bag_label_item
                        WHERE bag_code = ?
                        """, Long.class, firstBag);
        long secondLabelId = jdbc.queryForObject("""
                        SELECT id FROM rec_bag_label_item
                        WHERE bag_code = ?
                        """, Long.class, secondBag);
        verifyFactoryBagFixture(
                assetId, 1, firstBag, operatorId, firstLabelId);
        verifyFactoryBagFixture(
                assetId, 2, secondBag, operatorId, secondLabelId);

        JsonNode verified = factoryProgress(platform, hardwareSn);
        assertEquals(2, verified.path("factoryBags")
                .path("verifiedCount").asInt());
        assertTrue(verified.path("factoryBags")
                .path("complete").asBoolean());

        assertEquals(1, jdbc.update("""
                        UPDATE rec_bag_label_claim
                        SET released_at = UTC_TIMESTAMP(3),
                            release_reason = 'integration-test-release'
                        WHERE label_item_id = ?
                          AND released_at IS NULL
                        """, secondLabelId));
        JsonNode released = factoryProgress(platform, hardwareSn);
        assertEquals(1, released.path("factoryBags")
                .path("verifiedCount").asInt());
        assertFalse(released.path("factoryBags")
                .path("complete").asBoolean());
    }

    @Test
    void factoryProgressReturnsOnlyTheCurrentBagSnapshotAcceptanceTask()
            throws Exception {
        BrowserClient platform = new BrowserClient();
        login(platform, "/api/v1/web/platform/auth/sessions",
                platformLogin, PLATFORM_PASSWORD, 201);

        String hardwareSn = "HW-FACTORY-TASK-" + run;
        data(write(
                platform,
                post("/api/v1/web/platform/device-assets"),
                UUID.randomUUID(),
                Map.of(
                        "hardwareSn", hardwareSn,
                        "modelCode", "EC-M0",
                        "productionBatch", "FACTORY-TASK-" + run,
                        "expectedPortCount", 1),
                201));
        long assetId = assetId(hardwareSn);
        String currentDigest = "b".repeat(64);
        assertEquals(1, jdbc.update("""
                        UPDATE dev_device_asset
                        SET factory_bag_revision = 9,
                            factory_bag_set_sha256 = UNHEX(?),
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE id = ?
                        """, currentDigest, assetId));

        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        UUID currentTask = insertAcceptanceRequestTask(
                assetId, hardwareSn, 9, currentDigest, now);
        UUID newerButStaleTask = insertAcceptanceRequestTask(
                assetId, hardwareSn, 8, "a".repeat(64),
                now.plusSeconds(1));

        JsonNode current = factoryProgress(platform, hardwareSn);
        assertEquals(currentTask.toString(), current.path("acceptanceRequest")
                .path("taskUid").asText());
        assertFalse(newerButStaleTask.toString().equals(current
                .path("acceptanceRequest").path("taskUid").asText()));

        assertEquals(1, jdbc.update("""
                        UPDATE dev_device_asset
                        SET factory_bag_revision = 10,
                            factory_bag_set_sha256 = UNHEX(?),
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE id = ?
                        """, "c".repeat(64), assetId));
        JsonNode noCurrentTask = factoryProgress(platform, hardwareSn);
        assertTrue(noCurrentTask.path("acceptanceRequest")
                .path("taskUid").isNull());
    }

    @Test
    void runtimeViewUsesOneNetLifecycleFactWithoutInventingLiveHealth()
            throws Exception {
        BrowserClient platform = new BrowserClient();
        login(platform, "/api/v1/web/platform/auth/sessions",
                platformLogin, PLATFORM_PASSWORD, 201);

        String hardwareSn = "HW-RUNTIME-" + run;
        JsonNode created = data(write(
                platform,
                post("/api/v1/web/platform/device-assets"),
                UUID.randomUUID(),
                Map.of(
                        "hardwareSn", hardwareSn,
                        "modelCode", "EC-M0",
                        "productionBatch", "RUNTIME-" + run,
                        "expectedPortCount", 1),
                201));
        assertEquals("UNKNOWN",
                created.path("connectivity")
                        .path("oneNetConnectionStatus").asText());

        JsonNode unknownRuntime = data(read(platform,
                "/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/runtime",
                200));
        assertEquals("UNKNOWN",
                unknownRuntime.path("health")
                        .path("oneNetConnectionStatus").asText());
        assertEquals("UNKNOWN",
                unknownRuntime.path("health")
                        .path("edgeConnectionStatus").asText());
        assertTrue(unknownRuntime.path("health")
                .path("trustedRuntimeReceivedAt").isNull());
        assertEquals(0, unknownRuntime.path("ports").size());

        UUID presenceEventUid = UUID.randomUUID();
        TrustedInboxReceipt presenceReceipt = trustedInbox.receive(
                new TrustedInboxMessage(
                        "onenet.device-lifecycle",
                        "onenet-product:" + hardwareSn,
                        presenceEventUid.toString(),
                        "DEVICE_TRANSPORT_STATUS_CHANGED",
                        1,
                        "device-online-lifecycle-fact"
                                .getBytes(StandardCharsets.UTF_8),
                        objectMapper.writeValueAsString(Map.of(
                                "trustedSource", Map.of(
                                        "deviceName", hardwareSn),
                                "presence", Map.of(
                                        "status", "ONLINE",
                                        "observedAt",
                                        Instant.now().toString()))),
                        "ONENET_MQ",
                        "onenet:lifecycle-test-fixture",
                        presenceEventUid,
                        null,
                        TrustedInboxExecutionLane.DEVICE));
        assertEquals(
                TrustedInboxReceiptState.ACCEPTED,
                presenceReceipt.state());
        deviceInboxWorker.runBatch("runtime-presence-worker-" + run);
        assertEquals("DONE", jdbc.queryForObject("""
                        SELECT state
                        FROM ops_reliable_task
                        WHERE task_uid = ?
                        """,
                String.class,
                presenceReceipt.taskUid().toString()));

        JsonNode onlineAsset = data(read(platform,
                "/api/v1/web/platform/device-assets/" + hardwareSn,
                200));
        assertEquals("ONLINE",
                onlineAsset.path("connectivity")
                        .path("oneNetConnectionStatus").asText());
        assertEquals("LIFECYCLE_EVENT",
                onlineAsset.path("connectivity")
                        .path("evidenceSource").asText());

        JsonNode onlineRuntime = data(read(platform,
                "/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/runtime",
                200));
        assertEquals("ONLINE",
                onlineRuntime.path("health")
                        .path("oneNetConnectionStatus").asText());
        assertFalse(onlineRuntime.path("health")
                .path("oneNetStatusObservedAt").isNull());
        assertEquals("UNKNOWN",
                onlineRuntime.path("health")
                        .path("edgeConnectionStatus").asText());
        assertTrue(onlineRuntime.path("health")
                .path("trustedRuntimeReceivedAt").isNull());
        assertTrue(onlineRuntime.path("configuration")
                .path("latestPublishedVersion").isNull());
        assertFalse(onlineRuntime.path("occupied").asBoolean());
        assertFalse(onlineRuntime.path("fetchedAt").isNull());
    }

    @Test
    void cancelledAcceptanceChallengeLateV4EvidenceConvergesWithoutChangingAcceptanceOrSealAuthorization()
            throws Exception {
        BrowserClient platform = new BrowserClient();
        login(platform, "/api/v1/web/platform/auth/sessions",
                platformLogin, PLATFORM_PASSWORD, 201);

        String hardwareSn = "HW-LATE-ACCEPTANCE-" + run;
        data(write(
                platform,
                post("/api/v1/web/platform/device-assets"),
                UUID.randomUUID(),
                Map.of(
                        "hardwareSn", hardwareSn,
                        "modelCode", "EC-M0",
                        "productionBatch", "LATE-ACCEPTANCE-" + run,
                        "expectedPortCount", 1),
                201));
        long assetId = assetId(hardwareSn);
        long factoryBagRevision = 7L;
        String factoryBagSetSha256 = "7".repeat(64);
        assertEquals(1, jdbc.update("""
                        UPDATE dev_device_asset
                        SET factory_bag_revision = ?,
                            factory_bag_set_sha256 = UNHEX(?),
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE id = ?
                        """,
                factoryBagRevision,
                factoryBagSetSha256,
                assetId));
        seedCurrentAcceptedEvidenceFixture(
                assetId, factoryBagRevision, factoryBagSetSha256);
        JsonNode accepted = data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/acceptance-evaluations"),
                UUID.randomUUID(),
                Map.of(),
                200));
        assertEquals("PASSED", accepted.path("acceptanceStatus").asText());
        assertEquals(1L, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_factory_seal_authorization
                        WHERE asset_id = ?
                        """,
                Long.class,
                assetId));

        AssetAcceptanceSnapshot assetBefore =
                assetAcceptanceSnapshot(assetId);
        long evidenceCountBefore = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_acceptance_evidence
                        WHERE asset_id = ?
                        """,
                Long.class,
                assetId);
        FactorySealSnapshot sealBefore = factorySealSnapshot(assetId);

        UUID challengeUid = UUID.randomUUID();
        UUID commandUid = UUID.randomUUID();
        LocalDateTime taskTime = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        Map<String, Object> challengePayload = new LinkedHashMap<>();
        challengePayload.put("challengeUid", challengeUid.toString());
        challengePayload.put("expectedPortCount", 1);
        challengePayload.put("factoryBagRevision", factoryBagRevision);
        challengePayload.put(
                "factoryBagSetSha256", factoryBagSetSha256);
        Map<String, Object> challengeEnvelope = new LinkedHashMap<>();
        challengeEnvelope.put("schemaVersion", 2);
        challengeEnvelope.put("commandUid", commandUid.toString());
        challengeEnvelope.put(
                "commandType", "REQUEST_DEVICE_ACCEPTANCE");
        challengeEnvelope.put("targetDeviceName", hardwareSn);
        challengeEnvelope.put("target", Map.of(
                "type", "DEVICE_ASSET",
                "uid", hardwareSn));
        challengeEnvelope.put(
                "issuedAt", taskTime.toInstant(ZoneOffset.UTC).toString());
        challengeEnvelope.put(
                "expiresAt",
                taskTime.plusMinutes(10)
                        .toInstant(ZoneOffset.UTC)
                        .toString());
        challengeEnvelope.put("payloadSchemaVersion", 2);
        challengeEnvelope.put(
                "payloadSha256",
                OneNetCanonicalJson.payloadSha256(challengePayload));
        challengeEnvelope.put("payload", challengePayload);
        challengeEnvelope.put("cosGrant", null);
        UUID challengeTaskUid =
                reliableOperationsRepository.insertPlatformDeviceControlTask(
                        assetId,
                        "REQUEST_DEVICE_ACCEPTANCE",
                        "REQUEST_DEVICE_ACCEPTANCE:"
                                + challengeUid.toString().toUpperCase(),
                        "DEVICE_ASSET",
                        challengeUid.toString(),
                        2,
                        objectMapper.writeValueAsString(challengeEnvelope),
                        HexFormat.of().parseHex(
                                OneNetCanonicalJson.payloadSha256(
                                        challengeEnvelope)),
                        challengeUid,
                        commandUid,
                        1000,
                        null,
                        taskTime);
        assertEquals(1, jdbc.update("""
                        UPDATE ops_reliable_task
                        SET state = 'CANCELLED',
                            next_run_at = NULL,
                            lease_token = NULL,
                            lease_worker = NULL,
                            lease_until = NULL,
                            dispatch_wait_reason = NULL,
                            handled_wake_version = wake_version,
                            completed_at = UTC_TIMESTAMP(3),
                            blocked_reason_code = NULL,
                            blocked_diagnostic = NULL,
                            lock_version = lock_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE task_uid = ?
                          AND state = 'PENDING'
                        """,
                challengeTaskUid.toString()));

        UUID eventUid = UUID.randomUUID();
        TrustedInboxMessage message = new TrustedInboxMessage(
                "onenet.device-event",
                "onenet-product:" + hardwareSn,
                eventUid.toString(),
                "DEVICE_ACCEPTANCE_EVIDENCE",
                2,
                "cancelled-acceptance-v4"
                        .getBytes(StandardCharsets.UTF_8),
                acceptanceEvidencePayload(
                        hardwareSn,
                        eventUid,
                        commandUid,
                        challengeUid,
                        factoryBagRevision,
                        factoryBagSetSha256),
                "ONENET_MQ",
                "onenet:cancelled-acceptance-v4",
                eventUid,
                commandUid,
                TrustedInboxExecutionLane.DEVICE);
        TrustedInboxReceipt receipt = trustedInbox.receive(message);
        assertEquals(TrustedInboxReceiptState.ACCEPTED, receipt.state());

        ReliableWorkerBatchResult applied = deviceInboxWorker.runBatch(
                "cancelled-acceptance-worker-" + run);
        assertEquals(1, applied.claimed());
        assertEquals(1, applied.accepted());
        assertEquals(0, applied.failed());
        assertEquals("PROCESSED", jdbc.queryForObject("""
                        SELECT processing_state
                        FROM ops_inbox_message
                        WHERE inbox_uid = ?
                        """,
                String.class,
                receipt.inboxUid().toString()));
        assertEquals("DONE", jdbc.queryForObject("""
                        SELECT state
                        FROM ops_reliable_task
                        WHERE task_uid = ?
                        """,
                String.class,
                receipt.taskUid().toString()));
        assertEquals("NO_ACTION_REQUIRED", jdbc.queryForObject("""
                        SELECT attempt.technical_result
                        FROM ops_task_attempt attempt
                        JOIN ops_reliable_task task
                          ON task.id = attempt.task_id
                        WHERE task.task_uid = ?
                        """,
                String.class,
                receipt.taskUid().toString()));

        String confirmationTaskKey = "CONFIRM_EDGE_EVENT:"
                + eventUid.toString().toUpperCase();
        assertEquals(1L, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_reliable_task
                        WHERE task_key = ?
                          AND task_type = 'CONFIRM_EDGE_EVENT'
                        """,
                Long.class,
                confirmationTaskKey));
        assertEquals("BUSINESS_APPLIED|NO_ACTION_REQUIRED", jdbc.queryForObject("""
                        SELECT CONCAT(
                            JSON_UNQUOTE(JSON_EXTRACT(
                                redacted_execution_snapshot,
                                '$.payload.outcome')),
                            '|',
                            JSON_UNQUOTE(JSON_EXTRACT(
                                redacted_execution_snapshot,
                                '$.payload.effectKind')))
                        FROM ops_reliable_task
                        WHERE task_key = ?
                        """,
                String.class,
                confirmationTaskKey));

        TrustedInboxReceipt duplicate = trustedInbox.receive(message);
        assertEquals(
                TrustedInboxReceiptState.DUPLICATE_ACCEPTED,
                duplicate.state());
        assertEquals(receipt.inboxUid(), duplicate.inboxUid());
        assertEquals(receipt.taskUid(), duplicate.taskUid());
        ReliableWorkerBatchResult duplicateRun = deviceInboxWorker.runBatch(
                "cancelled-acceptance-duplicate-worker-" + run);
        assertEquals(1, duplicateRun.claimed());
        assertEquals(1, duplicateRun.accepted());
        assertEquals(0, duplicateRun.failed());
        assertEquals("DONE", jdbc.queryForObject("""
                        SELECT state
                        FROM ops_reliable_task
                        WHERE task_uid = ?
                        """,
                String.class,
                receipt.taskUid().toString()));
        assertEquals(2L, jdbc.queryForObject("""
                        SELECT delivery_count
                        FROM ops_inbox_message
                        WHERE inbox_uid = ?
                        """,
                Long.class,
                receipt.inboxUid().toString()));
        assertEquals(1L, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_reliable_task
                        WHERE task_key = ?
                          AND task_type = 'CONFIRM_EDGE_EVENT'
                        """,
                Long.class,
                confirmationTaskKey));
        assertEquals(2L, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_task_attempt attempt
                        JOIN ops_reliable_task task
                          ON task.id = attempt.task_id
                        WHERE task.task_uid = ?
                          AND attempt.technical_result =
                              'NO_ACTION_REQUIRED'
                        """,
                Long.class,
                receipt.taskUid().toString()));

        assertEquals(assetBefore, assetAcceptanceSnapshot(assetId));
        assertEquals(evidenceCountBefore, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_acceptance_evidence
                        WHERE asset_id = ?
                        """,
                Long.class,
                assetId));
        assertEquals(sealBefore, factorySealSnapshot(assetId));
    }

    @Test
    void permanentOwnershipPreservesHistoryAndCancelsStoppedDeviceWork()
            throws Exception {
        BrowserClient platform = new BrowserClient();
        login(platform, "/api/v1/web/platform/auth/sessions",
                platformLogin, PLATFORM_PASSWORD, 201);

        String tenantCode = code("tenant");
        String otherTenantCode = code("tenant-other");
        String organizationCode = code("org");
        String otherOrganizationCode = code("org-other");
        String principalLogin = "device-principal-" + run;
        createEnabledScope(platform, tenantCode, organizationCode,
                otherOrganizationCode, principalLogin);
        String globalEntryBaseUrl = "https://example.test/ecobin/device";
        long tenantId = jdbc.queryForObject(
                "SELECT id FROM iam_tenant WHERE tenant_code = ?",
                Long.class,
                tenantCode);
        long organizationId = jdbc.queryForObject("""
                        SELECT id FROM iam_organization
                        WHERE tenant_id = ? AND organization_code = ?
                        """,
                Long.class,
                tenantId,
                organizationCode);
        String appId = "wx" + UUID.randomUUID().toString()
                .replace("-", "").substring(0, 16);
        jdbc.update("""
                        INSERT INTO iam_miniapp_channel (
                            channel_uid, appid, display_name,
                            login_enabled, app_secret,
                            activated_at, lock_version,
                            configured_at, created_at, updated_at
                        ) VALUES (
                            ?, ?, 'Device QR channel', 1, 'test-secret',
                            UTC_TIMESTAMP(3), 0, UTC_TIMESTAMP(3),
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                """,
                UUID.randomUUID().toString(),
                appId);
        long channelId = jdbc.queryForObject(
                "SELECT id FROM iam_miniapp_channel WHERE appid = ?",
                Long.class,
                appId);
        jdbc.update("""
                        INSERT INTO iam_organization_miniapp_binding (
                            binding_uid, tenant_id, organization_id,
                            miniapp_channel_id, status, bound_at,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, 'ACTIVE', UTC_TIMESTAMP(3), 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                UUID.randomUUID().toString(),
                tenantId,
                organizationId,
                channelId);
        createAndEnableTenant(
                platform,
                otherTenantCode,
                "device-other-principal-" + run);

        BrowserClient principal = new BrowserClient();
        login(principal, "/api/v1/web/auth/sessions",
                principalLogin, PRINCIPAL_PASSWORD, 201);

        String hardwareSn = "HW-PERMANENT-" + run;
        String firstBag = bagCodeService.issue().value();
        String secondBag = bagCodeService.issue().value();
        JsonNode created = data(write(
                platform,
                post("/api/v1/web/platform/device-assets"),
                UUID.randomUUID(),
                Map.of(
                        "hardwareSn", hardwareSn,
                        "modelCode", "EC-M0",
                        "productionBatch", "BATCH-" + run,
                        "expectedPortCount", 2),
                201));
        long assetId = assetId(hardwareSn);
        seedFactoryBagFixtures(assetId, List.of(firstBag, secondBag));
        String deviceCode = created.path("deviceCode").asText();
        assertTrue(deviceCode.matches("Dv_[A-Za-z0-9_-]{24,61}"));
        assertEquals("NORMAL", created.path("lifecycleStatus").asText());
        assertEquals("PENDING", created.path("acceptanceStatus").asText());
        assertTrue(created.path("deviceEntryUrl").isNull());
        assertEquals("UNKNOWN",
                created.path("connectivity")
                        .path("oneNetConnectionStatus").asText());
        assertTrue(created.path("connectivity")
                .path("statusObservedAt").isNull());
        assertEquals(hardwareSn,
                created.path("oneNetMapping").path("deviceName").asText());
        assertEquals(2, jdbc.queryForObject("""
                        SELECT COUNT(*) FROM dev_factory_installed_bag bag
                        JOIN dev_device_asset asset ON asset.id = bag.asset_id
                        WHERE asset.hardware_sn = ?
                        """, Integer.class, hardwareSn));

        JsonNode blocked = json(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/tenant-assignments"),
                UUID.randomUUID(),
                Map.of("tenantCode", tenantCode, "expectedVersion", 0),
                422));
        assertEquals("DEVICE.ACCEPTANCE_REQUIRED",
                blocked.path("code").asText());

        seedAcceptedEvidenceFixture(hardwareSn);
        JsonNode reevaluated = data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/acceptance-evaluations"),
                UUID.randomUUID(),
                Map.of(),
                200));
        assertEquals("PASSED", reevaluated.path("acceptanceStatus").asText());
        assertEquals(1, reevaluated.path("version").asLong());
        sealCurrentAcceptanceFixture(assetId);
        JsonNode accepted = data(read(platform,
                "/api/v1/web/platform/device-assets/" + hardwareSn,
                200));
        assertEquals("PASSED", accepted.path("acceptanceStatus").asText());
        assertEquals(1, accepted.path("version").asLong());
        JsonNode evidence = data(read(platform,
                "/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/acceptance-evidence",
                200));
        assertEquals(1, evidence.size());
        assertEquals("PASSED",
                evidence.get(0).path("evaluationStatus").asText());

        JsonNode tenantAssigned = data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/tenant-assignments"),
                UUID.randomUUID(),
                Map.of("tenantCode", tenantCode, "expectedVersion", 1),
                200));
        assertEquals(tenantCode, tenantAssigned.path("tenantCode").asText());
        assertEquals(2, tenantAssigned.path("version").asLong());

        JsonNode wrongTenant = json(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/tenant-assignments"),
                UUID.randomUUID(),
                Map.of(
                        "tenantCode", otherTenantCode,
                        "expectedVersion", 2),
                409));
        assertEquals("DEVICE.TENANT_ASSIGNMENT_PERMANENT",
                wrongTenant.path("code").asText());

        long otherTenantId = jdbc.queryForObject(
                "SELECT id FROM iam_tenant WHERE tenant_code = ?",
                Long.class, otherTenantCode);
        assertThrows(DataAccessException.class, () -> jdbc.update("""
                        UPDATE dev_device_asset
                        SET tenant_id = ?, updated_at = UTC_TIMESTAMP(3)
                        WHERE id = ?
                        """, otherTenantId, assetId));

        JsonNode organizationAssigned = data(write(
                principal,
                post("/api/v1/web/device-assets/" + hardwareSn
                        + "/organization-assignments"),
                UUID.randomUUID(),
                Map.of(
                        "organizationCode", organizationCode,
                        "expectedVersion", 2),
                200));
        assertEquals(organizationCode,
                organizationAssigned.path("organizationCode").asText());
        assertEquals(
                globalEntryBaseUrl + "?deviceCode=" + deviceCode,
                organizationAssigned.path("deviceEntryUrl").asText());
        assertEquals(3, organizationAssigned.path("version").asLong());

        JsonNode wrongOrganization = json(write(
                principal,
                post("/api/v1/web/device-assets/" + hardwareSn
                        + "/organization-assignments"),
                UUID.randomUUID(),
                Map.of(
                        "organizationCode", otherOrganizationCode,
                        "expectedVersion", 3),
                409));
        assertEquals("DEVICE.ORGANIZATION_ASSIGNMENT_PERMANENT",
                wrongOrganization.path("code").asText());

        assertEquals(2, countByAsset("dev_port", assetId));
        assertEquals(2, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_bag bag
                        JOIN rec_bag_current_occupancy occupancy
                          ON occupancy.bag_id = bag.id
                        JOIN dev_port port ON port.id = occupancy.port_id
                        WHERE port.asset_id = ?
                        """, Integer.class, assetId));
        assertEquals(2, countByAsset("rec_port_capacity_state", assetId));
        assertEquals(1, countByAsset("dev_config_version", assetId));
        assertEquals("SYSTEM", jdbc.queryForObject("""
                        SELECT publication_source
                        FROM dev_config_version
                        WHERE asset_id = ?
                        """, String.class, assetId));
        assertEquals("PENDING", jdbc.queryForObject("""
                        SELECT application.status
                        FROM dev_config_application application
                        WHERE application.asset_id = ?
                        """, String.class, assetId));
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*) FROM ops_reliable_task
                        WHERE source_device_asset_id = ?
                          AND task_type = 'ENSURE_DEVICE_CONFIGURATION'
                          AND state = 'PENDING'
                        """, Integer.class, assetId));
        assertEquals("DEVICE_PRESENCE_UNKNOWN", jdbc.queryForObject("""
                        SELECT dispatch_wait_reason
                        FROM ops_reliable_task
                        WHERE source_device_asset_id = ?
                          AND task_type = 'ENSURE_DEVICE_CONFIGURATION'
                          AND state = 'PENDING'
                        """, String.class, assetId));

        JsonNode platformVersions = data(read(
                platform,
                "/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/configuration-versions?limit=20",
                200));
        assertEquals(1, platformVersions.path("items").size());
        assertEquals(1, platformVersions.path("items").get(0)
                .path("versionNo").asLong());
        String initialApplicationUid = platformVersions.path("items").get(0)
                .path("application").path("applicationUid").asText();
        JsonNode initialApplication = data(read(
                platform,
                "/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/configuration-applications/"
                        + initialApplicationUid,
                200));
        assertEquals("PENDING", initialApplication.path("status").asText());

        blockConfigurationTask(
                assetId,
                "DEVICE_IDENTITY_UNRESOLVED",
                "integration fixture: command was not delivered");
        JsonNode preDeliveryBlocked = data(read(
                platform,
                "/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/configuration-applications/"
                        + initialApplicationUid,
                200));
        assertEquals("RESYNCHRONIZE", preDeliveryBlocked
                .path("nextActions").get(0).asText());
        long applicationVersion = preDeliveryBlocked
                .path("version").asLong();
        JsonNode resynchronized = data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/configuration-applications/"
                        + initialApplicationUid
                        + "/resynchronizations"),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion", applicationVersion,
                        "reason", "修复 OneNet 设备身份后唤醒未送达命令"),
                202));
        assertEquals("PENDING", resynchronized.path("status").asText());
        assertEquals("PENDING",
                resynchronized.path("dispatchState").asText());
        assertEquals("PENDING", jdbc.queryForObject("""
                        SELECT state FROM ops_reliable_task
                        WHERE source_device_asset_id = ?
                          AND task_type = 'ENSURE_DEVICE_CONFIGURATION'
                        """, String.class, assetId));

        blockConfigurationTask(
                assetId,
                "DEVICE_EVIDENCE_TIMEOUT",
                "integration fixture: OneNet accepted without evidence");
        JsonNode evidenceTimeout = data(read(
                platform,
                "/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/configuration-applications/"
                        + initialApplicationUid,
                200));
        assertEquals("PUBLISH_NEW_CONFIGURATION", evidenceTimeout
                .path("nextActions").get(0).asText());
        JsonNode unsafeResynchronization = json(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/configuration-applications/"
                        + initialApplicationUid
                        + "/resynchronizations"),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion", applicationVersion,
                        "reason", "不应重复发送已被 OneNet 受理的命令"),
                409));
        assertEquals("DEVICE.CONFIGURATION_RESYNC_NOT_ALLOWED",
                unsafeResynchronization.path("code").asText());

        failConfigurationAfterEdgePersistence(assetId);
        JsonNode failedApplication = data(read(
                platform,
                "/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/configuration-applications/"
                        + initialApplicationUid,
                200));
        assertEquals("FAILED", failedApplication.path("status").asText());
        assertEquals("PUBLISH_NEW_CONFIGURATION", failedApplication
                .path("nextActions").get(0).asText());
        JsonNode failedResynchronization = json(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/configuration-applications/"
                        + initialApplicationUid
                        + "/resynchronizations"),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion", failedApplication
                                .path("version").asLong(),
                        "reason", "失败应用必须发布新版本"),
                409));
        assertEquals("DEVICE.CONFIGURATION_RESYNC_NOT_ALLOWED",
                failedResynchronization.path("code").asText());

        JsonNode rolledForward = data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/configuration-roll-forwards"),
                UUID.randomUUID(),
                Map.of(
                        "expectedLatestVersion", 1,
                        "reason", "恢复设备端同版本摘要冲突"),
                202));
        assertEquals(2, rolledForward.path("versionNo").asLong());
        assertEquals("PENDING", rolledForward.path("status").asText());
        assertTrue(rolledForward.path("statusUrl").asText().startsWith(
                "/api/v1/web/platform/device-assets/" + hardwareSn));
        assertEquals(2, countByAsset("dev_config_version", assetId));
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(DISTINCT content_sha256)
                        FROM dev_config_version
                        WHERE asset_id = ?
                        """, Integer.class, assetId));
        assertEquals(2, jdbc.queryForObject("""
                        SELECT COUNT(DISTINCT mcu_payload_sha256)
                        FROM dev_config_version
                        WHERE asset_id = ?
                        """, Integer.class, assetId));
        assertEquals("SYSTEM", jdbc.queryForObject("""
                        SELECT publication_source
                        FROM dev_config_version
                        WHERE asset_id = ? AND version_no = 2
                        """, String.class, assetId));
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*) FROM ops_reliable_task
                        WHERE source_device_asset_id = ?
                          AND task_type = 'ENSURE_DEVICE_CONFIGURATION'
                          AND state = 'PENDING'
                        """, Integer.class, assetId));
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*) FROM ops_reliable_task
                        WHERE source_device_asset_id = ?
                          AND task_type = 'ENSURE_DEVICE_CONFIGURATION'
                          AND state = 'CANCELLED'
                        """, Integer.class, assetId));

        JsonNode disabled = data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/disablements"),
                UUID.randomUUID(),
                Map.of("expectedVersion", 3, "reason", "现场停用检查"),
                200));
        assertEquals("DISABLED", disabled.path("lifecycleStatus").asText());
        JsonNode stoppedAcceptance = json(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/acceptance-evaluations"),
                UUID.randomUUID(), Map.of(), 409));
        assertEquals("DEVICE.ASSET_UNAVAILABLE", stoppedAcceptance.path("code").asText());
        read(principal, "/api/v1/web/device-assets/" + hardwareSn, 200);
        assertEquals(1, data(read(principal,
                "/api/v1/web/device-assets?page=1&pageSize=20",
                200)).path("items").size());
        String stoppedTaskUid = jdbc.queryForObject("""
                SELECT task_uid FROM ops_reliable_task WHERE source_device_asset_id = ?
                AND task_type = 'ENSURE_DEVICE_CONFIGURATION' ORDER BY id DESC LIMIT 1
                """, String.class, assetId);
        assertEquals("PENDING", jdbc.queryForObject(
                "SELECT state FROM ops_reliable_task WHERE task_uid = ?", String.class, stoppedTaskUid));
        assertEquals("DEVICE_DISABLED", jdbc.queryForObject("SELECT dispatch_wait_reason FROM ops_reliable_task WHERE task_uid = ?", String.class, stoppedTaskUid));
        reliableOperationsRepository.wakeTask(UUID.fromString(stoppedTaskUid), LocalDateTime.now(ZoneOffset.UTC));
        reliableOperationsRepository.wakeTaskForAuthorizedRedelivery(UUID.fromString(stoppedTaskUid),
                "ENSURE_DEVICE_CONFIGURATION", LocalDateTime.now(ZoneOffset.UTC));
        assertEquals("PENDING", jdbc.queryForObject(
                "SELECT state FROM ops_reliable_task WHERE task_uid = ?", String.class, stoppedTaskUid));
        assertEquals("PENDING", jdbc.queryForObject(
                "SELECT status FROM dev_config_application WHERE asset_id = ? ORDER BY id DESC LIMIT 1",
                String.class, assetId));
        jdbc.update("""
                UPDATE ops_reliable_task SET dispatch_wait_reason = 'AWAITING_DEVICE_EVIDENCE',
                    next_run_at = created_at WHERE task_uid = ?
                """, stoppedTaskUid);
        long stoppedTaskId = jdbc.queryForObject("SELECT id FROM ops_reliable_task WHERE task_uid = ?",
                Long.class, stoppedTaskUid);
        assertTrue(reliableOperationsRepository.lockExpiredDeviceEvidenceWaits(LocalDateTime.now(ZoneOffset.UTC))
                .stream().noneMatch(task -> task.taskId() == stoppedTaskId));
        read(principal, "/api/v1/web/organizations/" + organizationCode + "/devices/" + deviceCode
                + "/configuration-versions", 200);

        JsonNode restored = data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/restorations"),
                UUID.randomUUID(),
                Map.of("expectedVersion", 4, "reason", "检查完成"),
                200));
        assertEquals("NORMAL", restored.path("lifecycleStatus").asText());
        assertTrue(jdbc.queryForObject("SELECT next_run_at FROM ops_reliable_task WHERE task_uid = ?",
                LocalDateTime.class, stoppedTaskUid).isAfter(LocalDateTime.now(ZoneOffset.UTC).plusSeconds(90)));
        assertEquals("PENDING", jdbc.queryForObject(
                "SELECT state FROM ops_reliable_task WHERE task_uid = ?", String.class, stoppedTaskUid));
        assertEquals(2L, jdbc.queryForObject(
                "SELECT MAX(version_no) FROM dev_config_version WHERE asset_id = ?", Long.class, assetId));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM ops_reliable_task WHERE source_device_asset_id = ?
                AND task_type = 'ENSURE_DEVICE_CONFIGURATION' AND state = 'PENDING'
                """, Integer.class, assetId));
        read(principal, "/api/v1/web/device-assets/" + hardwareSn, 200);

        JsonNode retired = data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/retirements"),
                UUID.randomUUID(),
                Map.of("expectedVersion", 5, "reason", "永久报废"),
                200));
        assertEquals("RETIRED", retired.path("lifecycleStatus").asText());
        assertEquals("CANCELLED", jdbc.queryForObject(
                "SELECT state FROM ops_reliable_task WHERE task_uid = ?", String.class, stoppedTaskUid));
        reliableOperationsRepository.wakeTask(UUID.fromString(stoppedTaskUid), LocalDateTime.now(ZoneOffset.UTC));
        assertEquals("CANCELLED", jdbc.queryForObject(
                "SELECT state FROM ops_reliable_task WHERE task_uid = ?", String.class, stoppedTaskUid));
        assertEquals(0, data(read(principal,
                "/api/v1/web/device-assets?hardwareSn=" + hardwareSn, 200)).path("items").size());
        assertEquals(1, data(read(principal,
                "/api/v1/web/device-assets?lifecycleStatus=RETIRED&hardwareSn=" + hardwareSn, 200)).path("items").size());
        assertEquals(1, data(read(platform,
                "/api/v1/web/platform/device-assets?lifecycleStatus=ALL&hardwareSn=" + hardwareSn, 200)).path("items").size());
        assertEquals(0, data(read(platform,
                "/api/v1/web/platform/device-assets?hardwareSn=" + hardwareSn, 200)).path("items").size());
        read(principal, "/api/v1/web/device-assets/" + hardwareSn, 200);
        JsonNode cannotRestore = json(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/restorations"),
                UUID.randomUUID(),
                Map.of("expectedVersion", 6, "reason", "错误恢复尝试"),
                409));
        assertEquals("DEVICE.RETIRED_IS_FINAL",
                cannotRestore.path("code").asText());

        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM information_schema.tables
                        WHERE table_schema = DATABASE()
                          AND table_name IN (
                            'dev_device_deployment',
                            'dev_asset_tenant_allocation',
                            'dev_asset_active_tenant_allocation',
                            'dev_asset_active_deployment'
                          )
                        """, Integer.class));
    }

    @Test
    void appliedConfigurationAcceptsTheValidBaselineOfTheReplacementBag()
            throws Exception {
        BrowserClient platform = new BrowserClient();
        login(platform, "/api/v1/web/platform/auth/sessions",
                platformLogin, PLATFORM_PASSWORD, 201);

        String tenantCode = code("replacement-tenant");
        String organizationCode = code("replacement-org");
        String principalLogin = "replacement-principal-" + run;
        createAndEnableTenant(platform, tenantCode, principalLogin);
        createAndEnableOrganization(
                platform, tenantCode, organizationCode);

        BrowserClient principal = new BrowserClient();
        login(principal, "/api/v1/web/auth/sessions",
                principalLogin, PRINCIPAL_PASSWORD, 201);

        String hardwareSn = "HW-REPLACEMENT-" + run;
        String factoryBagCode = bagCodeService.issue().value();
        data(write(
                platform,
                post("/api/v1/web/platform/device-assets"),
                UUID.randomUUID(),
                Map.of(
                        "hardwareSn", hardwareSn,
                        "modelCode", "EC-M0",
                        "productionBatch", "BATCH-" + run,
                        "expectedPortCount", 1),
                201));
        seedFactoryBagFixtures(
                assetId(hardwareSn), List.of(factoryBagCode));
        seedAcceptedEvidenceFixture(hardwareSn);
        data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/acceptance-evaluations"),
                UUID.randomUUID(),
                Map.of(),
                200));
        sealCurrentAcceptanceFixture(assetId(hardwareSn));
        data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/tenant-assignments"),
                UUID.randomUUID(),
                Map.of(
                        "tenantCode", tenantCode,
                        "expectedVersion", 1),
                200));
        data(write(
                principal,
                post("/api/v1/web/device-assets/" + hardwareSn
                        + "/organization-assignments"),
                UUID.randomUUID(),
                Map.of(
                        "organizationCode", organizationCode,
                        "expectedVersion", 2),
                200));

        long assetId = assetId(hardwareSn);
        long tenantId = jdbc.queryForObject(
                "SELECT tenant_id FROM dev_device_asset WHERE id = ?",
                Long.class,
                assetId);
        long organizationId = jdbc.queryForObject(
                "SELECT organization_id FROM dev_device_asset WHERE id = ?",
                Long.class,
                assetId);
        ReplacementBagFacts replacement = seedReplacementBag(
                assetId,
                tenantId,
                organizationId,
                bagCodeService.issue().value());
        ConfigurationEventFacts configuration = configurationEventFacts(
                assetId);
        int measurementsBefore = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_port_baseline_measurement
                        WHERE asset_id = ?
                        """,
                Integer.class,
                assetId);
        long edgeEventSequence = jdbc.queryForObject("""
                        SELECT COALESCE(MAX(edge_event_sequence), 0) + 1
                        FROM dev_edge_event
                        WHERE asset_id = ?
                        """,
                Long.class,
                assetId);
        UUID eventUid = UUID.randomUUID();
        TrustedInboxScopeResolver scopeResolver =
                writer -> writer.organization(tenantId, organizationId);
        TrustedInboxReceipt receipt = trustedInbox.receive(
                new TrustedInboxMessage(
                        "onenet.device-event",
                        "onenet-product:" + hardwareSn,
                        eventUid.toString(),
                        "CONFIGURATION_PROGRESS",
                        2,
                        "replacement-bag-configuration-progress"
                                .getBytes(StandardCharsets.UTF_8),
                        configurationProgressPayload(
                                hardwareSn,
                                eventUid,
                                edgeEventSequence,
                                configuration),
                        "ONENET_MQ",
                        "onenet:test-fixture",
                        eventUid,
                        configuration.applicationUid(),
                        TrustedInboxExecutionLane.DEVICE,
                        scopeResolver));
        assertEquals(TrustedInboxReceiptState.ACCEPTED, receipt.state());

        deviceInboxWorker.runBatch("replacement-bag-worker-" + run);

        assertEquals("DONE", jdbc.queryForObject("""
                        SELECT state
                        FROM ops_reliable_task
                        WHERE task_uid = ?
                        """,
                String.class,
                receipt.taskUid().toString()));
        assertEquals("PROCESSED", jdbc.queryForObject("""
                        SELECT processing_state
                        FROM ops_inbox_message
                        WHERE inbox_uid = ?
                        """,
                String.class,
                receipt.inboxUid().toString()));
        assertEquals("APPLIED", jdbc.queryForObject("""
                        SELECT status
                        FROM dev_config_application
                        WHERE application_uid = ?
                        """,
                String.class,
                configuration.applicationUid().toString()));
        assertEquals(configuration.versionNo(), jdbc.queryForObject("""
                        SELECT applied_config_version_no
                        FROM dev_device_runtime_state
                        WHERE asset_id = ?
                        """,
                Long.class,
                assetId));
        assertEquals(replacement.bagId(), jdbc.queryForObject("""
                        SELECT occupancy.bag_id
                        FROM rec_bag_current_occupancy occupancy
                        JOIN dev_port port ON port.id = occupancy.port_id
                        WHERE port.asset_id = ?
                          AND occupancy.occupancy_type = 'PORT_BOUND'
                        """,
                Long.class,
                assetId));
        assertEquals(replacement.baselineId(), jdbc.queryForObject("""
                        SELECT current_baseline_id
                        FROM rec_port_capacity_state
                        WHERE asset_id = ?
                        """,
                Long.class,
                assetId));
        assertEquals(replacement.bagId(), jdbc.queryForObject("""
                        SELECT baseline.bag_id
                        FROM rec_port_capacity_state capacity
                        JOIN rec_port_weight_baseline baseline
                          ON baseline.id = capacity.current_baseline_id
                        WHERE capacity.asset_id = ?
                        """,
                Long.class,
                assetId));
        assertEquals(measurementsBefore, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_port_baseline_measurement
                        WHERE asset_id = ?
                        """,
                Integer.class,
                assetId));
    }

    private JsonNode factoryProgress(
            BrowserClient platform,
            String hardwareSn) throws Exception {
        return data(read(platform,
                "/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/factory-progress",
                200));
    }

    private void verifyFactoryBagFixture(
            long assetId,
            int portNo,
            String bagCode,
            long operatorId,
            long labelId) {
        assertEquals(1, jdbc.update("""
                        INSERT INTO dev_factory_installed_bag (
                            asset_id, port_no, bag_code,
                            installation_source,
                            installed_by_factory_operator_id,
                            label_item_id, tare_status,
                            last_failure_code, installed_at,
                            created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 'FACTORY_MINIAPP', ?, ?,
                            'PENDING', NULL, UTC_TIMESTAMP(3),
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                assetId,
                portNo,
                bagCode,
                operatorId,
                labelId));
        assertEquals(1, jdbc.update("""
                        INSERT INTO rec_bag_label_claim (
                            claim_uid, label_item_id, claim_kind,
                            asset_id, port_no,
                            claimed_by_factory_operator_id,
                            claimed_at, released_at, release_reason,
                            created_at
                        ) VALUES (?, ?, 'FACTORY_INSTALLATION', ?, ?, ?,
                                  UTC_TIMESTAMP(3), NULL, NULL,
                                  UTC_TIMESTAMP(3))
                        """,
                UUID.randomUUID().toString(),
                labelId,
                assetId,
                portNo,
                operatorId));
    }

    private void seedFactoryBagFixtures(
            long assetId,
            List<String> bagCodes) {
        long platformAdminId = jdbc.queryForObject("""
                        SELECT id FROM iam_platform_admin
                        WHERE login_name = ?
                        """, Long.class, platformLogin);
        String operatorCode = "OP_" + UUID.randomUUID().toString()
                .replace("-", "").substring(0, 12).toUpperCase();
        assertEquals(1, jdbc.update("""
                        INSERT INTO iam_factory_operator (
                            factory_operator_uid, operator_code,
                            display_name, enabled, auth_version,
                            lock_version, created_by_platform_admin_id,
                            created_at, updated_at
                        ) VALUES (?, ?, 'Device integration operator', 1,
                                  0, 0, ?, UTC_TIMESTAMP(3),
                                  UTC_TIMESTAMP(3))
                        """,
                UUID.randomUUID().toString(),
                operatorCode,
                platformAdminId));
        long operatorId = jdbc.queryForObject("""
                        SELECT id FROM iam_factory_operator
                        WHERE operator_code = ?
                        """, Long.class, operatorCode);

        UUID batchUid = UUID.randomUUID();
        assertEquals(1, jdbc.update("""
                        INSERT INTO rec_bag_label_batch (
                            batch_uid, operation_uid, key_id, label_count,
                            request_sha256, created_by_platform_admin_id,
                            created_at
                        ) VALUES (?, ?, 'K1', ?, UNHEX(SHA2(?, 256)), ?,
                                  UTC_TIMESTAMP(3))
                        """,
                batchUid.toString(),
                UUID.randomUUID().toString(),
                bagCodes.size(),
                "device-integration-factory-bags-" + UUID.randomUUID(),
                platformAdminId));
        long batchId = jdbc.queryForObject("""
                        SELECT id FROM rec_bag_label_batch
                        WHERE batch_uid = ?
                        """, Long.class, batchUid.toString());
        for (int index = 0; index < bagCodes.size(); index++) {
            String bagCode = bagCodes.get(index);
            int portNo = index + 1;
            assertEquals(1, jdbc.update("""
                            INSERT INTO rec_bag_label_item (
                                batch_id, sequence_no, bag_code, created_at
                            ) VALUES (?, ?, ?, UTC_TIMESTAMP(3))
                            """,
                    batchId,
                    portNo,
                    bagCode));
            long labelId = jdbc.queryForObject("""
                            SELECT id FROM rec_bag_label_item
                            WHERE batch_id = ? AND sequence_no = ?
                            """, Long.class, batchId, portNo);
            verifyFactoryBagFixture(
                    assetId, portNo, bagCode, operatorId, labelId);
        }
        String canonicalBagSet = java.util.stream.IntStream
                .range(0, bagCodes.size())
                .mapToObj(index -> (index + 1) + ":" + bagCodes.get(index))
                .collect(java.util.stream.Collectors.joining("\n"));
        assertEquals(1, jdbc.update("""
                        UPDATE dev_device_asset
                        SET factory_bag_revision =
                                factory_bag_revision + ?,
                            factory_bag_set_sha256 =
                                UNHEX(SHA2(?, 256)),
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE id = ?
                        """,
                bagCodes.size(),
                canonicalBagSet,
                assetId));
    }

    private UUID insertAcceptanceRequestTask(
            long assetId,
            String hardwareSn,
            long factoryBagRevision,
            String factoryBagSetSha256,
            LocalDateTime taskTime) {
        UUID challengeUid = UUID.randomUUID();
        UUID commandUid = UUID.randomUUID();
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("challengeUid", challengeUid.toString());
        payload.put("expectedPortCount", 1);
        payload.put("factoryBagRevision", factoryBagRevision);
        payload.put("factoryBagSetSha256", factoryBagSetSha256);
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", "REQUEST_DEVICE_ACCEPTANCE");
        envelope.put("targetDeviceName", hardwareSn);
        envelope.put("target", Map.of(
                "type", "DEVICE_ASSET",
                "uid", hardwareSn));
        envelope.put(
                "issuedAt", taskTime.toInstant(ZoneOffset.UTC).toString());
        envelope.put(
                "expiresAt",
                taskTime.plusMinutes(10)
                        .toInstant(ZoneOffset.UTC)
                        .toString());
        envelope.put("payloadSchemaVersion", 2);
        envelope.put(
                "payloadSha256",
                OneNetCanonicalJson.payloadSha256(payload));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        return reliableOperationsRepository.insertPlatformDeviceControlTask(
                assetId,
                "REQUEST_DEVICE_ACCEPTANCE",
                "REQUEST_DEVICE_ACCEPTANCE:"
                        + challengeUid.toString().toUpperCase(),
                "DEVICE_ASSET",
                challengeUid.toString(),
                2,
                objectMapper.writeValueAsString(envelope),
                HexFormat.of().parseHex(
                        OneNetCanonicalJson.payloadSha256(envelope)),
                challengeUid,
                commandUid,
                1000,
                null,
                taskTime);
    }

    private void seedCurrentAcceptedEvidenceFixture(
            long assetId,
            long factoryBagRevision,
            String factoryBagSetSha256) {
        UUID evidenceUid = UUID.randomUUID();
        UUID challengeUid = UUID.randomUUID();
        UUID commandUid = UUID.randomUUID();
        UUID edgeStoreInstanceUid = UUID.randomUUID();
        String evidenceSha256 = OneNetCanonicalJson.payloadSha256(Map.of(
                "fixture", "current-accepted-evidence",
                "evidenceUid", evidenceUid.toString()));
        assertEquals(1, jdbc.update("""
                        INSERT INTO dev_device_acceptance_evidence (
                            evidence_uid, asset_id,
                            challenge_uid, command_uid,
                            factory_bag_revision,
                            factory_bag_set_sha256,
                            evidence_schema_version,
                            edge_store_instance_uid,
                            edge_software_version,
                            edge_protocol_version,
                            mcu_firmware_version,
                            onenet_online,
                            persistent_store_healthy,
                            trusted_time_healthy,
                            clock_quality,
                            configuration_persistence_healthy,
                            mcu_communication_healthy,
                            mcu_remote_update_capable,
                            sensors_healthy,
                            cameras_capture_healthy,
                            camera_upload_healthy,
                            device_entry_url_stored,
                            device_entry_url_sha256,
                            mcu_simulated, cameras_simulated,
                            evaluation_status,
                            failure_reasons_json, evidence_json,
                            evidence_sha256,
                            observed_at, received_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, UNHEX(?), 4, ?,
                            '0.1.0', '2', 'fixed-frame-1.0.0',
                            1, 1, 1, 'SYNCED', 1, 1, 0,
                            1, 1, 1, 1, UNHEX(?), 0, 0,
                            'PASSED', JSON_ARRAY(),
                            JSON_OBJECT(
                                'evidenceSchemaVersion', 4,
                                'factoryBagRevision', ?,
                                'factoryBagSetSha256', ?
                            ),
                            UNHEX(?),
                            UTC_TIMESTAMP(3),
                            UTC_TIMESTAMP(3),
                            UTC_TIMESTAMP(3)
                        )
                        """,
                evidenceUid.toString(),
                assetId,
                challengeUid.toString(),
                commandUid.toString(),
                factoryBagRevision,
                factoryBagSetSha256,
                edgeStoreInstanceUid.toString(),
                "4".repeat(64),
                factoryBagRevision,
                factoryBagSetSha256,
                evidenceSha256));
    }

    private String acceptanceEvidencePayload(
            String hardwareSn,
            UUID eventUid,
            UUID commandUid,
            UUID challengeUid,
            long factoryBagRevision,
            String factoryBagSetSha256) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("evidenceSchemaVersion", 4);
        payload.put("challengeUid", challengeUid.toString());
        payload.put("factoryBagRevision", factoryBagRevision);
        payload.put("factoryBagSetSha256", factoryBagSetSha256);
        payload.put("edgeSoftwareVersion", "0.1.0");
        payload.put("edgeProtocolVersion", "2");
        payload.put("edgeStoreInstanceUid", UUID.randomUUID().toString());
        payload.put("mcuFirmwareVersion", "fixed-frame-1.0.0");
        payload.put("persistentStoreHealthy", true);
        payload.put("trustedTimeHealthy", true);
        payload.put("configurationPersistenceHealthy", true);
        payload.put("mcuCommunicationHealthy", true);
        payload.put("mcuRemoteUpdateCapable", false);
        payload.put("sensorsHealthy", true);
        payload.put("camerasCaptureHealthy", true);
        payload.put("cameraUploadHealthy", true);
        payload.put("deviceEntryUrlStored", true);
        payload.put("deviceEntryUrlSha256", "4".repeat(64));
        payload.put("mcuSimulated", false);
        payload.put("camerasSimulated", false);
        payload.put("verifiedPortCount", 1);
        payload.put("verifiedCameraCount", 2);
        payload.put("sensorSampleSha256", "d".repeat(64));
        payload.put("cameraCaptureSha256", "e".repeat(64));
        payload.put("cameraUploadSha256", "f".repeat(64));

        Map<String, Object> event = new LinkedHashMap<>();
        event.put("schemaVersion", 2);
        event.put("eventUid", eventUid.toString());
        event.put("edgeEventSequence", 1055);
        event.put("eventType", "DEVICE_ACCEPTANCE_EVIDENCE");
        event.put("deliveryClass", "RELIABLE_FACT");
        event.put("target", Map.of(
                "type", "DEVICE_ASSET",
                "uid", hardwareSn));
        event.put("commandUid", commandUid.toString());
        event.put("occurredAt", Instant.now().toString());
        event.put("clockQuality", "SYNCED");
        event.put(
                "payloadSha256",
                OneNetCanonicalJson.payloadSha256(payload));
        event.put("payload", payload);
        return objectMapper.writeValueAsString(Map.of(
                "trustedSource", Map.of(
                        "productId", "mysql-integration-product",
                        "deviceName", hardwareSn),
                "eventCanonicalSha256", "b".repeat(64),
                "event", event));
    }

    private AssetAcceptanceSnapshot assetAcceptanceSnapshot(long assetId) {
        return jdbc.queryForObject("""
                        SELECT acceptance_status,
                               acceptance_generation,
                               LOWER(HEX(acceptance_evidence_sha256))
                                   AS evidence_sha256,
                               accepted_at,
                               last_acceptance_evaluated_at,
                               acceptance_failure_json,
                               mcu_remote_update_capable,
                               control_version,
                               updated_at
                        FROM dev_device_asset
                        WHERE id = ?
                        """,
                (rs, ignored) -> new AssetAcceptanceSnapshot(
                        rs.getString("acceptance_status"),
                        rs.getLong("acceptance_generation"),
                        rs.getString("evidence_sha256"),
                        rs.getObject("accepted_at", LocalDateTime.class),
                        rs.getObject(
                                "last_acceptance_evaluated_at",
                                LocalDateTime.class),
                        rs.getString("acceptance_failure_json"),
                        rs.getObject(
                                "mcu_remote_update_capable",
                                Integer.class),
                        rs.getLong("control_version"),
                        rs.getObject("updated_at", LocalDateTime.class)),
                assetId);
    }

    private FactorySealSnapshot factorySealSnapshot(long assetId) {
        return jdbc.queryForObject("""
                        SELECT id, authorization_status,
                               command_uid, reliable_task_uid,
                               acknowledged_at, cancelled_at,
                               cancellation_reason,
                               completion_event_uid,
                               sealed_at, updated_at
                        FROM dev_factory_seal_authorization
                        WHERE asset_id = ?
                        """,
                (rs, ignored) -> new FactorySealSnapshot(
                        rs.getLong("id"),
                        rs.getString("authorization_status"),
                        rs.getString("command_uid"),
                        rs.getString("reliable_task_uid"),
                        rs.getObject(
                                "acknowledged_at", LocalDateTime.class),
                        rs.getObject(
                                "cancelled_at", LocalDateTime.class),
                        rs.getString("cancellation_reason"),
                        rs.getString("completion_event_uid"),
                        rs.getObject("sealed_at", LocalDateTime.class),
                        rs.getObject("updated_at", LocalDateTime.class)),
                assetId);
    }

    private void seedAcceptedEvidenceFixture(String hardwareSn) {
        long assetId = assetId(hardwareSn);
        long factoryBagRevision = jdbc.queryForObject("""
                        SELECT factory_bag_revision
                        FROM dev_device_asset
                        WHERE id = ?
                        """,
                Long.class,
                assetId);
        String factoryBagSetSha256 = jdbc.queryForObject("""
                        SELECT LOWER(HEX(factory_bag_set_sha256))
                        FROM dev_device_asset
                        WHERE id = ?
                        """,
                String.class,
                assetId);
        assertNotNull(factoryBagSetSha256);
        seedCurrentAcceptedEvidenceFixture(
                assetId, factoryBagRevision, factoryBagSetSha256);
    }

    private void sealCurrentAcceptanceFixture(long assetId) {
        String completionSeed = UUID.randomUUID().toString();
        assertEquals(1, jdbc.update("""
                        UPDATE dev_factory_seal_authorization authorization
                        JOIN dev_device_asset asset
                          ON asset.id = authorization.asset_id
                        SET authorization.authorization_status = 'SEALED',
                            completion_event_uid = ?,
                            completion_payload_sha256 =
                                UNHEX(SHA2(CONCAT(?, ':payload'), 256)),
                            image_release_id = 'integration-test-image',
                            image_release_sha256 =
                                UNHEX(SHA2(CONCAT(?, ':image'), 256)),
                            factory_report_sha256 =
                                UNHEX(SHA2(CONCAT(?, ':report'), 256)),
                            authorization_binding_sha256 =
                                UNHEX(SHA2(CONCAT(?, ':binding'), 256)),
                            operator_confirmation_uid = ?,
                            completion_clock_quality = 'SYNCED',
                            sealed_at = UTC_TIMESTAMP(3),
                            cleanup_completed_at = UTC_TIMESTAMP(3),
                            completion_received_at = UTC_TIMESTAMP(3),
                            authorization.updated_at = UTC_TIMESTAMP(3)
                        WHERE authorization.asset_id = ?
                          AND authorization.authorization_status IN (
                              'PENDING', 'ACKNOWLEDGED'
                          )
                          AND asset.acceptance_status = 'PASSED'
                          AND authorization.acceptance_generation =
                              asset.acceptance_generation
                          AND authorization.acceptance_evidence_sha256 =
                              asset.acceptance_evidence_sha256
                          AND authorization.factory_bag_revision =
                              asset.factory_bag_revision
                          AND authorization.factory_bag_set_sha256 =
                              asset.factory_bag_set_sha256
                        """,
                UUID.randomUUID().toString(),
                completionSeed,
                completionSeed,
                completionSeed,
                completionSeed,
                UUID.randomUUID().toString(),
                assetId));
        assertEquals(1, jdbc.update("""
                        UPDATE ops_reliable_task task
                        JOIN dev_factory_seal_authorization authorization
                          ON authorization.reliable_task_uid = task.task_uid
                        JOIN dev_device_asset asset
                          ON asset.id = authorization.asset_id
                        SET task.state = 'DONE',
                            task.next_run_at = NULL,
                            task.lease_token = NULL,
                            task.lease_worker = NULL,
                            task.lease_until = NULL,
                            task.dispatch_wait_reason = NULL,
                            task.consecutive_failure_count = 0,
                            task.handled_wake_version = task.wake_version,
                            task.completed_at = UTC_TIMESTAMP(3),
                            task.blocked_reason_code = NULL,
                            task.blocked_diagnostic = NULL,
                            task.lock_version = task.lock_version + 1,
                            task.updated_at = UTC_TIMESTAMP(3)
                        WHERE authorization.asset_id = ?
                          AND authorization.authorization_status = 'SEALED'
                          AND asset.acceptance_status = 'PASSED'
                          AND authorization.acceptance_generation =
                              asset.acceptance_generation
                          AND authorization.acceptance_evidence_sha256 =
                              asset.acceptance_evidence_sha256
                          AND authorization.factory_bag_revision =
                              asset.factory_bag_revision
                          AND authorization.factory_bag_set_sha256 =
                              asset.factory_bag_set_sha256
                          AND task.task_type = 'AUTHORIZE_FACTORY_SEAL'
                          AND task.state IN ('PENDING', 'BLOCKED')
                        """,
                assetId));
    }

    private ReplacementBagFacts seedReplacementBag(
            long assetId,
            long tenantId,
            long organizationId,
            String replacementBagCode) {
        // The clean workflow has its own integration coverage.  This fixture
        // reproduces only its durable result: historical factory baseline,
        // replacement occupancy, and a VALID baseline for that current bag.
        long portId = jdbc.queryForObject("""
                        SELECT id
                        FROM dev_port
                        WHERE asset_id = ? AND port_no = 1
                        """,
                Long.class,
                assetId);
        long factoryBagId = jdbc.queryForObject("""
                        SELECT occupancy.bag_id
                        FROM rec_bag_current_occupancy occupancy
                        WHERE occupancy.port_id = ?
                          AND occupancy.occupancy_type = 'PORT_BOUND'
                        """,
                Long.class,
                portId);
        long factoryEventId = jdbc.queryForObject("""
                        SELECT id
                        FROM rec_bag_occupancy_event
                        WHERE bag_id = ?
                          AND port_id = ?
                          AND event_type = 'INITIAL_INSTALLED'
                        """,
                Long.class,
                factoryBagId,
                portId);
        jdbc.update("""
                        INSERT INTO rec_port_weight_baseline (
                            tenant_id, organization_id, port_id, bag_id,
                            version_no, source_type, source_bag_event_id,
                            source_physical_result_id,
                            source_clean_record_id, source_measurement_id,
                            baseline_weight_g, established_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, 1, 'INITIAL_BINDING', ?,
                            NULL, NULL, NULL, 700,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                tenantId,
                organizationId,
                portId,
                factoryBagId,
                factoryEventId);

        UUID replacementBagUid = UUID.randomUUID();
        jdbc.update("""
                        INSERT INTO rec_bag (
                            bag_uid, tenant_id, organization_id,
                            bag_code, registered_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                replacementBagUid.toString(),
                tenantId,
                organizationId,
                replacementBagCode);
        long replacementBagId = jdbc.queryForObject("""
                        SELECT id
                        FROM rec_bag
                        WHERE bag_uid = ?
                        """,
                Long.class,
                replacementBagUid.toString());
        UUID replacementEventUid = UUID.randomUUID();
        jdbc.update("""
                        INSERT INTO rec_bag_occupancy_event (
                            event_uid, tenant_id, organization_id,
                            bag_id, port_id, clean_operation_id,
                            event_type, occurred_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, NULL, 'INITIAL_INSTALLED',
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                replacementEventUid.toString(),
                tenantId,
                organizationId,
                replacementBagId,
                portId);
        long replacementEventId = jdbc.queryForObject("""
                        SELECT id
                        FROM rec_bag_occupancy_event
                        WHERE event_uid = ?
                        """,
                Long.class,
                replacementEventUid.toString());
        jdbc.update("""
                        INSERT INTO rec_port_weight_baseline (
                            tenant_id, organization_id, port_id, bag_id,
                            version_no, source_type, source_bag_event_id,
                            source_physical_result_id,
                            source_clean_record_id, source_measurement_id,
                            baseline_weight_g, established_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, 2, 'INITIAL_BINDING', ?,
                            NULL, NULL, NULL, 800,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                tenantId,
                organizationId,
                portId,
                replacementBagId,
                replacementEventId);
        long replacementBaselineId = jdbc.queryForObject("""
                        SELECT id
                        FROM rec_port_weight_baseline
                        WHERE source_bag_event_id = ?
                        """,
                Long.class,
                replacementEventId);

        jdbc.update("""
                        DELETE FROM rec_bag_current_occupancy
                        WHERE port_id = ?
                          AND occupancy_type = 'PORT_BOUND'
                        """,
                portId);
        jdbc.update("""
                        INSERT INTO rec_bag_current_occupancy (
                            bag_id, tenant_id, organization_id,
                            occupancy_type, port_id,
                            clean_operation_id, acquired_at
                        ) VALUES (
                            ?, ?, ?, 'PORT_BOUND', ?, NULL,
                            UTC_TIMESTAMP(3)
                        )
                        """,
                replacementBagId,
                tenantId,
                organizationId,
                portId);
        jdbc.update("""
                        UPDATE rec_port_capacity_state
                        SET baseline_state = 'VALID',
                            current_baseline_id = ?,
                            current_baseline_weight_g = 800,
                            latest_stable_total_weight_g = 800,
                            raw_net_weight_g = 0,
                            displayed_fullness_percent = 0,
                            detection_gate = 'READY',
                            current_detection_id = NULL,
                            current_rule_fingerprint =
                                UNHEX(SHA2('replacement-fixture', 256)),
                            confirmed_fullness_state = 'NOT_FULL',
                            current_bag_id = ?,
                            lock_version = lock_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE asset_id = ? AND port_id = ?
                        """,
                replacementBaselineId,
                replacementBagId,
                assetId,
                portId);
        jdbc.update("""
                        UPDATE dev_factory_installed_bag
                        SET tare_status = 'READY',
                            last_failure_code = NULL,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE asset_id = ? AND port_no = 1
                        """,
                assetId);
        return new ReplacementBagFacts(
                replacementBagId,
                replacementBaselineId);
    }

    private ConfigurationEventFacts configurationEventFacts(long assetId) {
        return jdbc.queryForObject("""
                        SELECT application.application_uid,
                               command_row.command_uid,
                               version.version_no,
                               LOWER(HEX(version.content_sha256))
                                   AS content_sha256,
                               LOWER(HEX(version.mcu_payload_sha256))
                                   AS mcu_payload_sha256
                        FROM dev_config_application application
                        JOIN dev_config_version version
                          ON version.id = application.config_version_id
                        JOIN dev_device_command command_row
                          ON command_row.config_application_id = application.id
                         AND command_row.command_type = 'APPLY_CONFIGURATION'
                        WHERE application.asset_id = ?
                        ORDER BY version.version_no DESC
                        LIMIT 1
                        """,
                (rs, ignored) -> new ConfigurationEventFacts(
                        UUID.fromString(rs.getString("application_uid")),
                        UUID.fromString(rs.getString("command_uid")),
                        rs.getLong("version_no"),
                        rs.getString("content_sha256"),
                        rs.getString("mcu_payload_sha256")),
                assetId);
    }

    private void blockConfigurationTask(
            long assetId,
            String reasonCode,
            String diagnostic) {
        assertEquals(1, jdbc.update("""
                        UPDATE ops_reliable_task
                        SET state = 'BLOCKED',
                            next_run_at = NULL,
                            lease_token = NULL,
                            lease_worker = NULL,
                            lease_until = NULL,
                            dispatch_wait_reason = NULL,
                            handled_wake_version = wake_version,
                            completed_at = UTC_TIMESTAMP(3),
                            blocked_reason_code = ?,
                            blocked_diagnostic = ?,
                            lock_version = lock_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE source_device_asset_id = ?
                          AND task_type = 'ENSURE_DEVICE_CONFIGURATION'
                          AND state = 'PENDING'
                        """,
                reasonCode,
                diagnostic,
                assetId));
    }

    private void failConfigurationAfterEdgePersistence(long assetId) {
        assertEquals(1, jdbc.update("""
                        UPDATE dev_config_application application
                        JOIN dev_config_version version
                          ON version.id = application.config_version_id
                        SET application.status = 'FAILED',
                            application.reported_version_no =
                                version.version_no,
                            application.reported_content_sha256 =
                                version.content_sha256,
                            application.reported_mcu_payload_sha256 =
                                version.mcu_payload_sha256,
                            application.edge_persisted_at =
                                UTC_TIMESTAMP(3),
                            application.mcu_synced_at = NULL,
                            application.applied_at = NULL,
                            application.last_failure_at =
                                UTC_TIMESTAMP(3),
                            application.last_failure_code =
                                'MCU_APPLY_FAILED',
                            application.lock_version =
                                application.lock_version + 1,
                            application.updated_at = UTC_TIMESTAMP(3)
                        WHERE application.asset_id = ?
                          AND application.status = 'PENDING'
                        """,
                assetId));
    }

    private String configurationProgressPayload(
            String hardwareSn,
            UUID eventUid,
            long edgeEventSequence,
            ConfigurationEventFacts configuration) throws Exception {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "applicationUid",
                configuration.applicationUid().toString());
        payload.put("stage", "APPLIED");
        payload.put("version", configuration.versionNo());
        payload.put("contentSha256", configuration.contentSha256());
        payload.put(
                "mcuPayloadSha256",
                configuration.mcuPayloadSha256());
        payload.put("mcuCommandUid", UUID.randomUUID().toString());
        payload.put("errorCode", null);

        Map<String, Object> event = new LinkedHashMap<>();
        event.put("schemaVersion", 2);
        event.put("eventUid", eventUid.toString());
        event.put("edgeEventSequence", edgeEventSequence);
        event.put("eventType", "CONFIGURATION_PROGRESS");
        event.put("deliveryClass", "RELIABLE_FACT");
        event.put("target", Map.of(
                "type", "CONFIGURATION_APPLICATION",
                "uid", configuration.applicationUid().toString()));
        event.put("commandUid", configuration.commandUid().toString());
        event.put("occurredAt", Instant.now().toString());
        event.put("clockQuality", "SYNCED");
        event.put("payloadSha256", "a".repeat(64));
        event.put("payload", payload);

        return objectMapper.writeValueAsString(Map.of(
                "trustedSource", Map.of(
                        "productId", "mysql-integration-product",
                        "deviceName", hardwareSn),
                "eventCanonicalSha256", "b".repeat(64),
                "event", event));
    }

    private long assetId(String hardwareSn) {
        Long value = jdbc.queryForObject(
                "SELECT id FROM dev_device_asset WHERE hardware_sn = ?",
                Long.class, hardwareSn);
        assertNotNull(value);
        return value;
    }

    private int countByAsset(String table, long assetId) {
        if (!java.util.Set.of(
                "dev_port",
                "rec_port_capacity_state",
                "dev_config_version").contains(table)) {
            throw new IllegalArgumentException("unexpected test table");
        }
        Integer value = jdbc.queryForObject(
                "SELECT COUNT(*) FROM " + table + " WHERE asset_id = ?",
                Integer.class, assetId);
        return value == null ? 0 : value;
    }

    private void createEnabledScope(
            BrowserClient platform,
            String tenantCode,
            String organizationCode,
            String otherOrganizationCode,
            String principalLogin) throws Exception {
        createAndEnableTenant(platform, tenantCode, principalLogin);
        createAndEnableOrganization(platform, tenantCode, organizationCode);
        createAndEnableOrganization(
                platform, tenantCode, otherOrganizationCode);
    }

    private void createAndEnableTenant(
            BrowserClient platform,
            String tenantCode,
            String principalLogin) throws Exception {
        data(write(
                platform,
                post("/api/v1/web/platform/tenants"),
                UUID.randomUUID(),
                Map.of(
                        "tenantCode", tenantCode,
                        "enterpriseName", "Device test tenant"),
                201));
        write(platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/principal-account"),
                UUID.randomUUID(),
                Map.of(
                        "loginName", principalLogin,
                        "initialPassword", PRINCIPAL_PASSWORD,
                        "displayName", "Device principal",
                        "expectedVersion", 0),
                201);
        JsonNode tenant = data(read(platform,
                "/api/v1/web/platform/tenants/" + tenantCode,
                200));
        write(platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/activations"),
                UUID.randomUUID(),
                Map.of("expectedVersion", tenant.path("version").asLong()),
                200);
    }

    private void createAndEnableOrganization(
            BrowserClient platform,
            String tenantCode,
            String organizationCode) throws Exception {
        JsonNode organization = data(write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/organizations"),
                UUID.randomUUID(),
                Map.of(
                        "organizationCode", organizationCode,
                        "organizationName", "Device test organization"),
                201));
        write(platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/organizations/" + organizationCode
                        + "/activations"),
                UUID.randomUUID(),
                Map.of("expectedVersion",
                        organization.path("version").asLong()),
                200);
    }

    private MvcResult login(
            BrowserClient client,
            String path,
            String loginName,
            String password,
            int expectedStatus) throws Exception {
        return write(client, post(path), null,
                Map.of("loginName", loginName, "password", password),
                expectedStatus);
    }

    private MvcResult read(
            BrowserClient client,
            String path,
            int expectedStatus) throws Exception {
        MvcResult result = mockMvc.perform(
                        withCookies(get(path), client))
                .andReturn();
        client.accept(result);
        assertEquals(expectedStatus, result.getResponse().getStatus(),
                result.getResponse().getContentAsString());
        return result;
    }

    private MvcResult write(
            BrowserClient client,
            MockHttpServletRequestBuilder request,
            UUID operationUid,
            Object body,
            int expectedStatus) throws Exception {
        String csrfToken = csrf(client);
        request = withCookies(request, client)
                .header("X-CSRF-TOKEN", csrfToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsBytes(body));
        if (operationUid != null) {
            request.header("Idempotency-Key", operationUid.toString());
        }
        MvcResult result = mockMvc.perform(request).andReturn();
        client.accept(result);
        assertEquals(expectedStatus, result.getResponse().getStatus(),
                result.getResponse().getContentAsString());
        return result;
    }

    private String csrf(BrowserClient client) throws Exception {
        MvcResult result = mockMvc.perform(withCookies(
                        get("/api/v1/web/auth/csrf-token"), client))
                .andReturn();
        client.accept(result);
        assertEquals(200, result.getResponse().getStatus());
        return data(result).path("token").asText();
    }

    private MockHttpServletRequestBuilder withCookies(
            MockHttpServletRequestBuilder request,
            BrowserClient client) {
        Cookie[] cookies = client.cookies.values().stream()
                .map(Cookie::clone)
                .toArray(Cookie[]::new);
        return cookies.length == 0 ? request : request.cookie(cookies);
    }

    private JsonNode data(MvcResult result) throws Exception {
        return json(result).path("data");
    }

    private JsonNode json(MvcResult result) throws Exception {
        return objectMapper.readTree(
                result.getResponse().getContentAsByteArray());
    }

    private String code(String prefix) {
        return prefix + "-" + run;
    }

    private record AssetAcceptanceSnapshot(
            String acceptanceStatus,
            long acceptanceGeneration,
            String acceptanceEvidenceSha256,
            LocalDateTime acceptedAt,
            LocalDateTime lastAcceptanceEvaluatedAt,
            String acceptanceFailureJson,
            Integer mcuRemoteUpdateCapable,
            long controlVersion,
            LocalDateTime updatedAt) {
    }

    private record FactorySealSnapshot(
            long id,
            String authorizationStatus,
            String commandUid,
            String reliableTaskUid,
            LocalDateTime acknowledgedAt,
            LocalDateTime cancelledAt,
            String cancellationReason,
            String completionEventUid,
            LocalDateTime sealedAt,
            LocalDateTime updatedAt) {
    }

    private record ReplacementBagFacts(
            long bagId,
            long baselineId) {
    }

    private record ConfigurationEventFacts(
            UUID applicationUid,
            UUID commandUid,
            long versionNo,
            String contentSha256,
            String mcuPayloadSha256) {
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class ProbeConfiguration {

        @Bean
        @Primary
        AcceptedSubmissionProbe acceptedSubmissionProbe() {
            return new AcceptedSubmissionProbe();
        }
    }

    static final class AcceptedSubmissionProbe
            implements ReliableDeviceCommandSubmissionPort {

        private int submissionCount;
        private DeviceCommandSubmission lastSubmission;
        private DeviceCommandSubmissionResult.Outcome nextOutcome =
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED;

        @Override
        public synchronized DeviceCommandSubmissionResult submit(
                DeviceCommandSubmission submission) {
            submissionCount++;
            lastSubmission = submission;
            DeviceCommandSubmissionResult.Outcome outcome = nextOutcome;
            nextOutcome =
                    DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED;
            return new DeviceCommandSubmissionResult(
                    outcome,
                    digest((byte) 1),
                    digest((byte) 2),
                    200,
                    outcome == DeviceCommandSubmissionResult.Outcome
                            .TARGET_OFFLINE
                            ? "ONENET_10421" : null,
                    null,
                    outcome == DeviceCommandSubmissionResult.Outcome
                            .TARGET_OFFLINE
                            ? "isolated test target is offline"
                            : "isolated test platform accepted");
        }

        synchronized int submissionCount() {
            return submissionCount;
        }

        synchronized DeviceCommandSubmission lastSubmission() {
            return lastSubmission;
        }

        synchronized void respondNextWith(
                DeviceCommandSubmissionResult.Outcome outcome) {
            nextOutcome = outcome;
        }

        synchronized void reset() {
            submissionCount = 0;
            lastSubmission = null;
            nextOutcome =
                    DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED;
        }

        private static byte[] digest(byte fill) {
            byte[] digest = new byte[32];
            Arrays.fill(digest, fill);
            return digest;
        }
    }

    private static final class BrowserClient {

        private final Map<String, Cookie> cookies = new LinkedHashMap<>();

        private void accept(MvcResult result) {
            for (Cookie cookie : result.getResponse().getCookies()) {
                if (cookie.getMaxAge() == 0) {
                    cookies.remove(cookie.getName());
                } else {
                    cookies.put(cookie.getName(), cookie);
                }
            }
        }
    }
}
