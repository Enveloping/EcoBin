package org.enveloping.ecobin;

import jakarta.servlet.http.Cookie;
import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceTransportPresencePort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.integration.cos.CosProperties;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetEventDispatcher;
import org.enveloping.ecobin.integration.onenet.outbound.OneNetProperties;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.reliability.DeviceTelemetryRetentionPort;
import org.enveloping.ecobin.operations.api.reliability.DeviceTaskGateReconciliationPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceCommandWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceInboxWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
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
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

@SpringBootTest(properties = {
        "spring.datasource.url=${ECOBIN_DEVICE_MYSQL_URL}",
        "spring.datasource.username=${ECOBIN_DEVICE_MYSQL_USERNAME}",
        "spring.datasource.password=${ECOBIN_DEVICE_MYSQL_PASSWORD}",
        "spring.sql.init.mode=never",
        "ecobin.database.epoch.test-bypass=false",
        "ecobin.external.mode=fake",
        "ecobin.external.fake.block-inbound=true",
        "ecobin.operations.reliable.workers-enabled=false",
        "ecobin.operations.reliable.iot-device.batch-size=1",
        "onenet.subscription.enabled=false",
        "jwt.secret=DEVICE_TEST_SECRET_MUST_BE_AT_LEAST_32_BYTES_LONG",
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
    private ReliableDeviceCommandWorkerPort worker;
    @Autowired
    private ReliableDeviceInboxWorkerPort inboxWorker;
    @Autowired
    private TrustedInboxPort trustedInboxPort;
    @Autowired
    private TrustedDeviceSourceScopePort sourceScopePort;
    @Autowired
    private TrustedDeviceTransportPresencePort transportPresencePort;
    @Autowired
    private AcceptedSubmissionProbe submissionProbe;
    @Autowired
    private DeviceConfigurationCanonicalizer canonicalizer;
    @Autowired
    private DeviceTaskGateReconciliationPort taskGateReconciliation;
    @Autowired
    private DeviceTelemetryRetentionPort telemetryRetention;

    private String run;
    private String platformLogin;

    @BeforeEach
    void seedPlatformAdministrator() {
        run = Long.toUnsignedString(System.nanoTime(), 36);
        platformLogin = "device-platform-" + run;
        submissionProbe.reset();
        deferPriorIntegrationTasks();
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

    private void deferPriorIntegrationTasks() {
        jdbc.update("""
                UPDATE ops_reliable_task task
                LEFT JOIN dev_device_deployment deployment
                  ON deployment.tenant_id = task.tenant_id
                 AND deployment.organization_id =
                     task.organization_id
                 AND deployment.id =
                     task.source_device_deployment_id
                LEFT JOIN dev_device_asset asset
                  ON asset.id = deployment.asset_id
                LEFT JOIN ops_inbox_message inbox
                  ON inbox.id = task.source_inbox_id
                SET task.next_run_at =
                    DATE_ADD(UTC_TIMESTAMP(3), INTERVAL 1 DAY),
                    task.updated_at = UTC_TIMESTAMP(3),
                    task.lock_version = task.lock_version + 1
                WHERE task.state = 'PENDING'
                  AND task.lease_token IS NULL
                  AND (
                      asset.hardware_sn LIKE 'HW-DEVICE-%'
                      OR inbox.source_principal_key LIKE
                          '%HW-DEVICE-%'
                  )
                """);
    }

    @Test
    void assetDeploymentConfigurationAndReliableDispatchRemainHonest()
            throws Exception {
        BrowserClient platform = new BrowserClient();
        login(
                platform,
                "/api/v1/web/platform/auth/sessions",
                platformLogin,
                PLATFORM_PASSWORD,
                201);

        String tenantCode = code("tenant");
        String organizationCode = code("org");
        String principalLogin = "device-principal-" + run;
        createEnabledScope(
                platform,
                tenantCode,
                organizationCode,
                principalLogin);
        BrowserClient principal = new BrowserClient();
        login(
                principal,
                "/api/v1/web/auth/sessions",
                principalLogin,
                PRINCIPAL_PASSWORD,
                201);

        String hardwareSn = "HW-DEVICE-" + run;
        UUID assetOperation = UUID.randomUUID();
        Map<String, Object> assetBody = Map.of(
                "hardwareSn", hardwareSn,
                "modelCode", "EC-M0",
                "productionBatch", "BATCH-" + run,
                "expectedPortCount", 2);
        JsonNode asset = data(write(
                platform,
                post("/api/v1/web/platform/device-assets"),
                assetOperation,
                assetBody,
                201));
        assertEquals("IN_STOCK", asset.path("lifecycleStatus").asText());
        assertEquals(0, asset.path("version").asLong());
        data(write(
                platform,
                post("/api/v1/web/platform/device-assets"),
                assetOperation,
                assetBody,
                201));
        MvcResult reusedKey = write(
                platform,
                post("/api/v1/web/platform/device-assets"),
                assetOperation,
                Map.of(
                        "hardwareSn", hardwareSn,
                        "modelCode", "DIFFERENT",
                        "productionBatch", "BATCH-" + run,
                        "expectedPortCount", 2),
                409);
        assertEquals(
                "COMMON.IDEMPOTENCY_KEY_REUSED",
                json(reusedKey).path("code").asText());
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*) FROM dev_device_asset
                        WHERE hardware_sn = ?
                        """, Integer.class, hardwareSn));

        JsonNode allocation = data(write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/device-asset-allocations"),
                UUID.randomUUID(),
                Map.of(
                        "hardwareSn", hardwareSn,
                        "expectedAssetVersion", 0,
                        "reason", "device integration allocation"),
                201));
        assertEquals("ACTIVE",
                allocation.path("allocationStatus").asText());
        assertEquals(0,
                allocation.path("allocationVersion").asLong());

        String organizationDeploymentBase =
                "/api/v1/web/organizations/" + organizationCode
                        + "/device-deployments";
        String deploymentBase = "/api/v1/web/platform/tenants/"
                + tenantCode + "/organizations/" + organizationCode
                + "/device-deployments";
        JsonNode deployment = data(write(
                principal,
                post(organizationDeploymentBase),
                UUID.randomUUID(),
                Map.of(
                        "allocationUid",
                        allocation.path("allocationUid").asText(),
                        "expectedAllocationVersion", 0),
                201));
        String deploymentCode =
                deployment.path("deploymentCode").asText();
        assertFalse(deploymentCode.isBlank());
        assertEquals(
                "COMMISSIONING",
                deployment.path("lifecycleStatus").asText());
        assertEquals(2, deployment.path("portCount").asInt());
        assertEquals(
                1,
                deployment.path("latestConfigurationVersion").asLong());
        assertEquals(
                "PENDING",
                deployment.path("configurationApplicationStatus").asText());
        assertFalse(deployment.path("businessEnabled").asBoolean());
        String applicationUid = jdbc.queryForObject("""
                        SELECT application.application_uid
                        FROM dev_config_application application
                        JOIN dev_device_deployment deployment
                          ON deployment.id = application.deployment_id
                        JOIN dev_config_version version
                          ON version.id =
                             application.config_version_id
                        WHERE deployment.public_code = ?
                          AND version.version_no = 1
                        """,
                String.class,
                deploymentCode);
        assertNotNull(applicationUid);
        assertEquals(
                2,
                data(read(
                        platform,
                        deploymentBase + "/" + deploymentCode + "/ports",
                        200)).size());

        JsonNode runtime = data(read(
                platform,
                deploymentBase + "/" + deploymentCode + "/runtime",
                200));
        assertFalse(runtime.path("deliveryAllowed").asBoolean());
        assertTrue(contains(
                runtime.path("deliveryBlockers"),
                "CONFIGURATION_NOT_APPLIED"));
        assertTrue(contains(
                runtime.path("deliveryBlockers"),
                "EDGE_OFFLINE"));
        JsonNode portRuntime = data(read(
                platform,
                deploymentBase + "/" + deploymentCode
                        + "/ports/1/runtime",
                200));
        assertEquals(
                "UNKNOWN",
                portRuntime.path("cleanDoorPhysicalState").asText());
        assertEquals(
                "NOT_OBSERVABLE",
                portRuntime.path("cleanDoorStateBasis").asText());

        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_config_version version
                        JOIN dev_config_application application
                          ON application.config_version_id = version.id
                        WHERE application.application_uid = ?
                          AND version.version_no = 1
                        """, Integer.class, applicationUid));
        assertEquals(
                "STAFF|DIGITAL_INFRARED|5|3",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    version.publication_source, '|',
                                    snapshot.fullness_sensor_kind, '|',
                                    snapshot.fullness_sample_count, '|',
                                    snapshot.fullness_min_valid_sample_count
                                )
                                FROM dev_config_version version
                                JOIN dev_config_application application
                                  ON application.config_version_id =
                                     version.id
                                JOIN dev_port_config_snapshot snapshot
                                  ON snapshot.config_version_id = version.id
                                 AND snapshot.port_id = (
                                     SELECT port.id
                                     FROM dev_port port
                                     WHERE port.deployment_id =
                                         version.deployment_id
                                       AND port.port_no = 1
                                 )
                                WHERE application.application_uid = ?
                                """,
                        String.class,
                        applicationUid));
        assertEquals(2, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_port_config_snapshot snapshot
                        JOIN dev_config_version version
                          ON version.id = snapshot.config_version_id
                        JOIN dev_config_application application
                          ON application.config_version_id = version.id
                        WHERE application.application_uid = ?
                          AND version.version_no = 1
                        """, Integer.class, applicationUid));
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_command command_row
                        JOIN dev_config_application application
                          ON application.id =
                             command_row.config_application_id
                        WHERE application.application_uid = ?
                          AND JSON_UNQUOTE(JSON_EXTRACT(
                            command_row.semantic_payload,
                            '$.commandType')) = 'APPLY_CONFIGURATION'
                          AND JSON_CONTAINS_PATH(
                            command_row.semantic_payload,
                            'all',
                            '$.issuedAt',
                            '$.expiresAt',
                            '$.payloadSha256',
                            '$.payload',
                            '$.cosGrant') = 1
                        """, Integer.class, applicationUid));
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_reliable_task task
                        JOIN dev_device_command command_row
                          ON command_row.id =
                             task.source_device_command_id
                        JOIN dev_config_application application
                          ON application.id =
                             command_row.config_application_id
                        WHERE application.application_uid = ?
                          AND task.task_type =
                              'ENSURE_DEVICE_CONFIGURATION'
                          AND task.state = 'PENDING'
                          AND JSON_CONTAINS_PATH(
                            task.redacted_execution_snapshot,
                            'one',
                            '$.payload') = 0
                        """, Integer.class, applicationUid));

        long lifecycleTime = Instant.now().toEpochMilli();
        acceptTransportLifecycle(
                hardwareSn,
                "ONLINE",
                lifecycleTime,
                "configuration-initial-online-" + run);
        ReliableWorkerBatchResult batch =
                worker.runBatch("device-integration-worker");
        assertEquals(1, batch.claimed());
        assertEquals(1, batch.accepted());
        assertEquals(0, batch.failed());
        assertEquals(1, submissionProbe.submissionCount());
        DeviceCommandSubmission submitted =
                submissionProbe.lastSubmission();
        assertNotNull(submitted);
        assertEquals(hardwareSn, submitted.hardwareSn());
        assertEquals(
                "APPLY_CONFIGURATION", submitted.commandType());
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_task_attempt attempt
                        JOIN ops_reliable_task task
                          ON task.id = attempt.task_id
                        JOIN dev_device_command command_row
                          ON command_row.id =
                             task.source_device_command_id
                        JOIN dev_config_application application
                          ON application.id =
                             command_row.config_application_id
                        WHERE application.application_uid = ?
                          AND attempt.technical_result =
                              'TECHNICAL_SUCCESS'
                          AND attempt.action_kind = 'SUBMIT'
                          AND attempt.request_sha256 IS NOT NULL
                          AND attempt.response_sha256 IS NOT NULL
                        """, Integer.class, applicationUid));
        assertEquals("PENDING", jdbc.queryForObject("""
                        SELECT task.state
                        FROM ops_reliable_task task
                        JOIN dev_device_command command_row
                          ON command_row.id =
                             task.source_device_command_id
                        JOIN dev_config_application application
                          ON application.id =
                             command_row.config_application_id
                        WHERE application.application_uid = ?
                          AND task.task_type =
                              'ENSURE_DEVICE_CONFIGURATION'
                        """, String.class, applicationUid));
        assertEquals(
                "AWAITING_DEVICE_EVIDENCE",
                jdbc.queryForObject("""
                                SELECT task.dispatch_wait_reason
                                FROM ops_reliable_task task
                                JOIN dev_device_command command_row
                                  ON command_row.id =
                                     task.source_device_command_id
                                JOIN dev_config_application application
                                  ON application.id =
                                     command_row.config_application_id
                                WHERE application.application_uid = ?
                                  AND task.task_type =
                                      'ENSURE_DEVICE_CONFIGURATION'
                                """,
                        String.class,
                        applicationUid));
        ReliableWorkerBatchResult acceptedAgain =
                worker.runBatch("device-accepted-no-resend-worker");
        assertEquals(0, acceptedAgain.claimed());
        assertEquals(1, submissionProbe.submissionCount());
        assertEquals("PENDING", jdbc.queryForObject("""
                        SELECT status FROM dev_config_application
                        WHERE application_uid = ?
                        """, String.class, applicationUid));

        JsonNode application = data(read(
                platform,
                deploymentBase + "/" + deploymentCode
                        + "/configuration-applications/" + applicationUid,
                200));
        assertEquals("PENDING", application.path("status").asText());
        assertEquals("PENDING", application.path("dispatchState").asText());
        assertEquals("WAIT", application.path("nextActions").get(0).asText());

        MvcResult acceptance = write(
                platform,
                post(deploymentBase + "/" + deploymentCode
                        + "/acceptances"),
                UUID.randomUUID(),
                Map.of(
                        "expectedDeploymentVersion", 0,
                        "expectedConfigurationVersion", 1,
                        "deliveryDoorObservedNormal", true,
                        "camerasObservedNormal", true,
                        "cleanDoorInstallationObservedNormal", true,
                        "reason", "isolated integration verification"),
                422);
        JsonNode problem = json(acceptance);
        assertEquals(
                "DEVICE.DEPLOYMENT_NOT_ACCEPTABLE",
                problem.path("code").asText());
        assertTrue(contains(
                problem.path("details").path("blockers"),
                "CONFIGURATION_NOT_APPLIED"));

        MvcResult prematureResync = write(
                platform,
                post(deploymentBase + "/" + deploymentCode
                        + "/configuration-applications/" + applicationUid
                        + "/resynchronizations"),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion", 0,
                        "reason", "must still be converging"),
                409);
        assertEquals(
                "DEVICE.CONFIGURATION_RESYNC_NOT_ALLOWED",
                json(prematureResync).path("code").asText());

        EdgeFactEvidence configurationEvidence =
                applyTrustedConfigurationProgress(
                hardwareSn,
                deploymentCode,
                applicationUid);
        assertEquals("ORGANIZATION|PROCESSED", jdbc.queryForObject("""
                        SELECT CONCAT(scope_kind, '|', processing_state)
                        FROM ops_inbox_message
                        WHERE message_kind = 'CONFIGURATION_PROGRESS'
                          AND external_message_id = (
                              SELECT event_uid
                              FROM dev_edge_event
                              WHERE target_type =
                                  'CONFIGURATION_APPLICATION'
                                AND target_stable_key_sha256 =
                                    UNHEX(SHA2(?, 256))
                          )
                        """, String.class, applicationUid));
        assertEquals("APPLIED", jdbc.queryForObject("""
                        SELECT status
                        FROM dev_config_application
                        WHERE application_uid = ?
                        """, String.class, applicationUid));
        assertEquals("DONE", jdbc.queryForObject("""
                        SELECT task.state
                        FROM ops_reliable_task task
                        JOIN dev_device_command command_row
                          ON command_row.id =
                             task.source_device_command_id
                        JOIN dev_config_application application
                          ON application.id =
                             command_row.config_application_id
                        WHERE application.application_uid = ?
                          AND task.task_type =
                              'ENSURE_DEVICE_CONFIGURATION'
                        """, String.class, applicationUid));
        assertEquals(1L, jdbc.queryForObject("""
                        SELECT applied_config_version_no
                        FROM dev_deployment_runtime_state runtime_state
                        JOIN dev_device_deployment deployment
                          ON deployment.id =
                             runtime_state.deployment_id
                        WHERE deployment.public_code = ?
                        """, Long.class, deploymentCode));
        JsonNode appliedApplication = data(read(
                platform,
                deploymentBase + "/" + deploymentCode
                        + "/configuration-applications/"
                        + applicationUid,
                200));
        assertEquals(
                "APPLIED",
                appliedApplication.path("status").asText());
        assertEquals(
                "DONE",
                appliedApplication.path("dispatchState").asText());

        Map<String, Object> confirmationTask = jdbc.queryForMap("""
                SELECT
                    task.target_stable_key AS confirmation_uid,
                    task.state,
                    task.max_auto_attempts,
                    task.source_device_command_id,
                    CAST(task.redacted_execution_snapshot AS CHAR)
                        AS execution_envelope
                FROM ops_reliable_task task
                WHERE task.task_key = ?
                  AND task.task_type = 'CONFIRM_EDGE_EVENT'
                  AND task.source_device_deployment_id = (
                      SELECT deployment.id
                      FROM dev_device_deployment deployment
                      WHERE deployment.public_code = ?
                  )
                """,
                "CONFIRM_EDGE_EVENT:"
                        + configurationEvidence.eventUid()
                        .toUpperCase(),
                deploymentCode);
        assertEquals(
                "PENDING", confirmationTask.get("state").toString());
        assertEquals(
                100,
                ((Number) confirmationTask.get("max_auto_attempts"))
                        .intValue());
        assertEquals(
                null, confirmationTask.get("source_device_command_id"));
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_command
                        WHERE command_type = 'CONFIRM_EDGE_EVENT'
                        """, Integer.class));

        JsonNode confirmationEnvelope = objectMapper.readTree(
                confirmationTask.get("execution_envelope").toString());
        assertEquals(
                configurationEvidence.eventUid(),
                confirmationEnvelope.path("payload")
                        .path("originalEventUid").asText());
        assertEquals(
                configurationEvidence.payloadSha256(),
                confirmationEnvelope.path("payload")
                        .path("originalPayloadSha256").asText());
        assertEquals(
                "BUSINESS_APPLIED",
                confirmationEnvelope.path("payload")
                        .path("outcome").asText());

        acceptTransportLifecycle(
                hardwareSn,
                "OFFLINE",
                lifecycleTime + 1,
                "configuration-offline-" + run);
        assertEquals("OFFLINE|DEVICE_OFFLINE", jdbc.queryForObject("""
                        SELECT CONCAT(
                            transport.onenet_connection_status,
                            '|', task.dispatch_wait_reason
                        )
                        FROM ops_reliable_task task
                        JOIN dev_device_deployment deployment
                          ON deployment.id =
                             task.source_device_deployment_id
                        JOIN dev_device_transport_state transport
                          ON transport.asset_id = deployment.asset_id
                        WHERE task.task_key = ?
                        """,
                String.class,
                "CONFIRM_EDGE_EVENT:"
                        + configurationEvidence.eventUid()
                        .toUpperCase()));
        ReliableWorkerBatchResult offlineBatch =
                worker.runBatch("device-offline-integration-worker");
        assertEquals(0, offlineBatch.claimed());
        assertEquals(1, submissionProbe.submissionCount());
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_task_attempt attempt
                        JOIN ops_reliable_task task
                          ON task.id = attempt.task_id
                        WHERE task.task_key = ?
                        """,
                Integer.class,
                "CONFIRM_EDGE_EVENT:"
                        + configurationEvidence.eventUid()
                        .toUpperCase()));

        acceptTransportLifecycle(
                hardwareSn,
                "ONLINE",
                lifecycleTime + 2,
                "configuration-online-" + run);
        assertEquals("ONLINE", jdbc.queryForObject("""
                        SELECT transport.onenet_connection_status
                        FROM dev_device_transport_state transport
                        JOIN dev_device_asset asset
                          ON asset.id = transport.asset_id
                        WHERE asset.hardware_sn = ?
                        """, String.class, hardwareSn));
        assertEquals(null, jdbc.queryForObject("""
                        SELECT dispatch_wait_reason
                        FROM ops_reliable_task
                        WHERE task_key = ?
                        """,
                String.class,
                "CONFIRM_EDGE_EVENT:"
                        + configurationEvidence.eventUid()
                        .toUpperCase()));

        List<ReliableWorkerBatchResult> confirmationBatches =
                runConcurrentDeviceBatches("device-confirmation");
        assertEquals(1, confirmationBatches.stream()
                .mapToInt(ReliableWorkerBatchResult::claimed)
                .sum());
        assertEquals(1, confirmationBatches.stream()
                .mapToInt(ReliableWorkerBatchResult::accepted)
                .sum());
        assertEquals(0, confirmationBatches.stream()
                .mapToInt(ReliableWorkerBatchResult::failed)
                .sum());
        assertEquals(2, submissionProbe.submissionCount());
        assertEquals(
                "CONFIRM_EDGE_EVENT",
                submissionProbe.lastSubmission().commandType());
        assertEquals(
                "PENDING",
                jdbc.queryForObject("""
                                SELECT state
                                FROM ops_reliable_task
                                WHERE task_key = ?
                                """,
                        String.class,
                        "CONFIRM_EDGE_EVENT:"
                                + configurationEvidence.eventUid()
                                .toUpperCase()));

        EdgeFactEvidence receiptEvidence =
                applyBusinessConfirmationReceipt(
                        hardwareSn,
                        deploymentCode,
                        confirmationEnvelope,
                        2);
        assertEquals(
                "DONE",
                jdbc.queryForObject("""
                                SELECT state
                                FROM ops_reliable_task
                                WHERE task_key = ?
                                """,
                        String.class,
                        "CONFIRM_EDGE_EVENT:"
                                + configurationEvidence.eventUid()
                                .toUpperCase()));
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_reliable_task
                        WHERE task_key = ?
                        """,
                Integer.class,
                "CONFIRM_EDGE_EVENT:"
                        + receiptEvidence.eventUid().toUpperCase()));

        EdgeFactEvidence runtimeEvidence =
                applyTrustedRuntimeSnapshot(
                        hardwareSn,
                        deploymentCode,
                        applicationUid,
                        3);
        assertEquals("ONLINE|ONLINE|READY", jdbc.queryForObject("""
                        SELECT CONCAT(
                            edge_connection_status, '|',
                            mcu_link_status, '|',
                            uart_state
                        )
                        FROM dev_deployment_runtime_state runtime
                        JOIN dev_device_deployment deployment
                          ON deployment.id = runtime.deployment_id
                        WHERE deployment.public_code = ?
                        """, String.class, deploymentCode));
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_reliable_task
                        WHERE task_key = ?
                        """,
                Integer.class,
                "CONFIRM_EDGE_EVENT:"
                        + runtimeEvidence.eventUid().toUpperCase()));

        JsonNode accepted = data(write(
                platform,
                post(deploymentBase + "/" + deploymentCode
                        + "/acceptances"),
                UUID.randomUUID(),
                Map.of(
                        "expectedDeploymentVersion", 0,
                        "expectedConfigurationVersion", 1,
                        "deliveryDoorObservedNormal", true,
                        "camerasObservedNormal", true,
                        "cleanDoorInstallationObservedNormal", true,
                        "reason",
                        "trusted Orange Pi runtime accepted"),
                200));
        assertEquals(1,
                accepted.path("configurationVersion").asLong());
        JsonNode activated = data(read(
                platform,
                deploymentBase + "/" + deploymentCode,
                200));
        assertEquals(
                "ENABLED",
                activated.path("lifecycleStatus").asText());
        JsonNode businessEnabled = data(write(
                principal,
                post(organizationDeploymentBase + "/" + deploymentCode
                        + "/business-switch/enablements"),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion", 1,
                        "reason",
                        "trusted Orange Pi runtime accepted"),
                200));
        assertTrue(
                businessEnabled.path("businessEnabled").asBoolean());
        JsonNode enabledRuntime = data(read(
                platform,
                deploymentBase + "/" + deploymentCode + "/runtime",
                200));
        assertTrue(enabledRuntime.path("deliveryAllowed").asBoolean());
        assertEquals(
                "ONLINE",
                enabledRuntime.path("health")
                        .path("mcuLinkStatus").asText());
        assertEquals(
                "READY",
                enabledRuntime.path("health")
                        .path("uartState").asText());

        String faultUid = UUID.randomUUID().toString();
        EdgeFactEvidence faultEvidence = applyTrustedFaultFact(
                "deviceFaultObserved",
                "device-fault-observed.event.json",
                "device-fault-observed.event-wire.json",
                hardwareSn,
                deploymentCode,
                faultUid,
                4,
                48);
        assertEquals(
                "OPEN|BUSINESS_BLOCKING|1",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    status, '|',
                                    impact_level, '|',
                                    discovery_count
                                )
                                FROM dev_device_fault_event
                                WHERE fault_uid = ?
                                """,
                        String.class,
                        faultUid));
        assertPendingConfirmation(faultEvidence);
        submissionProbe.respondNextWith(
                DeviceCommandSubmissionResult.Outcome.TARGET_OFFLINE);
        ReliableWorkerBatchResult targetOfflineBatch =
                worker.runBatch("device-target-offline-worker");
        assertEquals(1, targetOfflineBatch.claimed());
        assertEquals(0, targetOfflineBatch.accepted());
        assertEquals(1, targetOfflineBatch.failed());
        assertEquals("PENDING|DEVICE_OFFLINE|0|TARGET_OFFLINE",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    task.state, '|',
                                    task.dispatch_wait_reason, '|',
                                    task.consecutive_failure_count, '|',
                                    attempt.technical_result
                                )
                                FROM ops_reliable_task task
                                JOIN ops_task_attempt attempt
                                  ON attempt.task_id = task.id
                                WHERE task.task_key = ?
                                ORDER BY attempt.id DESC
                                LIMIT 1
                                """,
                        String.class,
                        "CONFIRM_EDGE_EVENT:"
                                + faultEvidence.eventUid().toUpperCase()));
        long staleRuntimeInboxId = jdbc.queryForObject("""
                        SELECT edge_event.source_inbox_id
                        FROM dev_deployment_runtime_state runtime
                        JOIN dev_device_deployment deployment
                          ON deployment.id = runtime.deployment_id
                        JOIN dev_edge_event edge_event
                          ON edge_event.id =
                              runtime.trusted_runtime_edge_event_id
                        WHERE deployment.public_code = ?
                        """,
                Long.class,
                deploymentCode);
        transportPresencePort.observeAuthenticatedMessage(
                hardwareSn, staleRuntimeInboxId);
        assertEquals("ONLINE|DEVICE_OFFLINE", jdbc.queryForObject("""
                        SELECT CONCAT(
                            transport.onenet_connection_status, '|',
                            task.dispatch_wait_reason
                        )
                        FROM dev_device_transport_state transport
                        JOIN dev_device_asset asset
                          ON asset.id = transport.asset_id
                        JOIN dev_device_deployment deployment
                          ON deployment.asset_id = asset.id
                         AND deployment.ended_at IS NULL
                        JOIN ops_reliable_task task
                          ON task.source_device_deployment_id = deployment.id
                        WHERE asset.hardware_sn = ?
                          AND task.task_key = ?
                        """,
                String.class,
                hardwareSn,
                "CONFIRM_EDGE_EVENT:"
                        + faultEvidence.eventUid().toUpperCase()));
        JsonNode faultBlockedRuntime = data(read(
                platform,
                deploymentBase + "/" + deploymentCode + "/runtime",
                200));
        assertFalse(
                faultBlockedRuntime.path("deliveryAllowed").asBoolean());
        assertEquals(
                "OPERATION_BLOCKED",
                faultBlockedRuntime.path("health")
                        .path("safetyStatus").asText());
        assertTrue(contains(
                faultBlockedRuntime.path("deliveryBlockers"),
                "SAFETY_LOCKED"));

        EdgeFactEvidence recoveryEvidence = applyTrustedFaultFact(
                "deviceFaultRecovered",
                "device-fault-recovered.event.json",
                "device-fault-recovered.event-wire.json",
                hardwareSn,
                deploymentCode,
                faultUid,
                5,
                49);
        assertEquals("ONLINE", jdbc.queryForObject("""
                        SELECT transport.onenet_connection_status
                        FROM dev_device_transport_state transport
                        JOIN dev_device_asset asset
                          ON asset.id = transport.asset_id
                        WHERE asset.hardware_sn = ?
                        """, String.class, hardwareSn));
        assertEquals(null, jdbc.queryForObject("""
                        SELECT dispatch_wait_reason
                        FROM ops_reliable_task
                        WHERE task_key = ?
                        """,
                String.class,
                "CONFIRM_EDGE_EVENT:"
                        + faultEvidence.eventUid().toUpperCase()));
        assertEquals(
                "RECOVERED|DEVICE_REPORTED|1",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    status, '|',
                                    recovery_method, '|',
                                    device_recovery_observed_edge_event_id
                                        IS NOT NULL
                                )
                                FROM dev_device_fault_event
                                WHERE fault_uid = ?
                                """,
                        String.class,
                        faultUid));
        assertPendingConfirmation(recoveryEvidence);
        JsonNode recoveredRuntime = data(read(
                platform,
                deploymentBase + "/" + deploymentCode + "/runtime",
                200));
        assertTrue(recoveredRuntime.path("deliveryAllowed").asBoolean());
        assertEquals(
                "SAFE",
                recoveredRuntime.path("health")
                        .path("safetyStatus").asText());

        EdgeFactEvidence alarmEvidence = applyTrustedSafetyState(
                hardwareSn,
                deploymentCode,
                "ALARM",
                6,
                50);
        assertEquals(
                "SAFETY_BLOCKED|SAFETY_BLOCKED",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    deployment_runtime.safety_status, '|',
                                    port_runtime.safety_status
                                )
                                FROM dev_deployment_runtime_state
                                    deployment_runtime
                                JOIN dev_device_deployment deployment
                                  ON deployment.id =
                                     deployment_runtime.deployment_id
                                JOIN dev_port port
                                  ON port.deployment_id = deployment.id
                                 AND port.port_no = 2
                                JOIN dev_port_runtime_state port_runtime
                                  ON port_runtime.port_id = port.id
                                WHERE deployment.public_code = ?
                                """,
                        String.class,
                        deploymentCode));
        assertPendingConfirmation(alarmEvidence);
        JsonNode alarmRuntime = data(read(
                platform,
                deploymentBase + "/" + deploymentCode + "/runtime",
                200));
        assertFalse(alarmRuntime.path("deliveryAllowed").asBoolean());
        assertTrue(contains(
                alarmRuntime.path("deliveryBlockers"),
                "SAFETY_LOCKED"));

        EdgeFactEvidence safeEvidence = applyTrustedSafetyState(
                hardwareSn,
                deploymentCode,
                "NORMAL",
                7,
                51);
        assertEquals(
                "SAFE|SAFE",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    deployment_runtime.safety_status, '|',
                                    port_runtime.safety_status
                                )
                                FROM dev_deployment_runtime_state
                                    deployment_runtime
                                JOIN dev_device_deployment deployment
                                  ON deployment.id =
                                     deployment_runtime.deployment_id
                                JOIN dev_port port
                                  ON port.deployment_id = deployment.id
                                 AND port.port_no = 2
                                JOIN dev_port_runtime_state port_runtime
                                  ON port_runtime.port_id = port.id
                                WHERE deployment.public_code = ?
                                """,
                        String.class,
                        deploymentCode));
        assertPendingConfirmation(safeEvidence);
        JsonNode safeRuntime = data(read(
                platform,
                deploymentBase + "/" + deploymentCode + "/runtime",
                200));
        assertTrue(safeRuntime.path("deliveryAllowed").asBoolean());
        assertEquals(
                "ONLINE",
                safeRuntime.path("health")
                        .path("mcuLinkStatus").asText());
        assertEquals(
                "READY",
                safeRuntime.path("health")
                        .path("uartState").asText());

        EdgeFactEvidence conflictEvidence =
                applyTrustedSafetyState(
                        hardwareSn,
                        deploymentCode,
                        "ALARM",
                        7,
                        52);
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_edge_event
                        WHERE event_uid = ?
                        """,
                Integer.class,
                conflictEvidence.eventUid()));
        String quarantineUid = jdbc.queryForObject("""
                        SELECT quarantine.quarantine_uid
                        FROM ops_message_quarantine quarantine
                        JOIN ops_inbox_message inbox
                          ON inbox.id =
                             quarantine.conflicting_inbox_id
                        WHERE inbox.external_message_id = ?
                          AND quarantine.reason_code =
                              'IDENTITY_CONTENT_CONFLICT'
                        """,
                String.class,
                conflictEvidence.eventUid());
        assertNotNull(quarantineUid);
        Map<String, Object> quarantineConfirmation =
                jdbc.queryForMap("""
                        SELECT
                            task.state,
                            task.source_device_command_id,
                            CAST(
                                task.redacted_execution_snapshot
                                AS CHAR
                            ) AS execution_envelope
                        FROM ops_reliable_task task
                        WHERE task.task_key = ?
                          AND task.task_type =
                              'CONFIRM_EDGE_EVENT'
                        """,
                        "CONFIRM_EDGE_EVENT:"
                                + conflictEvidence.eventUid()
                                .toUpperCase()
                                + ":"
                                + conflictEvidence.payloadSha256()
                                .toUpperCase());
        assertEquals(
                "PENDING",
                quarantineConfirmation.get("state").toString());
        assertEquals(
                null,
                quarantineConfirmation.get(
                        "source_device_command_id"));
        JsonNode quarantineEnvelope = objectMapper.readTree(
                quarantineConfirmation.get(
                        "execution_envelope").toString());
        assertEquals(
                "EVENT_QUARANTINED",
                quarantineEnvelope.path("payload")
                        .path("outcome").asText());
        assertEquals(
                "EVENT_IDENTITY_CONFLICT",
                quarantineEnvelope.path("payload")
                        .path("errorCode").asText());
        assertTrue(
                quarantineEnvelope.path("payload")
                        .path("effectKind").isNull());
        assertEquals(
                quarantineUid,
                quarantineEnvelope.path("payload")
                        .path("quarantineUid").asText());
        assertEquals(
                conflictEvidence.payloadSha256(),
                quarantineEnvelope.path("payload")
                        .path("originalPayloadSha256").asText());
        JsonNode stillSafeRuntime = data(read(
                platform,
                deploymentBase + "/" + deploymentCode + "/runtime",
                200));
        assertTrue(
                stillSafeRuntime.path("deliveryAllowed").asBoolean());
        assertEquals(
                "SAFE",
                stillSafeRuntime.path("health")
                        .path("safetyStatus").asText());

        applyTrustedRuntimeSnapshot(
                hardwareSn,
                deploymentCode,
                applicationUid,
                8);
        jdbc.update("""
                        UPDATE ops_inbox_message
                        SET created_at = DATE_SUB(
                                UTC_TIMESTAMP(3), INTERVAL 2 DAY),
                            first_received_at = DATE_SUB(
                                UTC_TIMESTAMP(3), INTERVAL 2 DAY),
                            last_received_at = DATE_SUB(
                                UTC_TIMESTAMP(3), INTERVAL 2 DAY),
                            processed_at = DATE_SUB(
                                UTC_TIMESTAMP(3), INTERVAL 2 DAY),
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE external_message_id = ?
                        """,
                runtimeEvidence.eventUid());
        assertEquals(
                1,
                telemetryRetention.purgeRuntimeSnapshotsBefore(
                        Instant.now().minus(1, ChronoUnit.DAYS),
                        100));
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_inbox_message
                        WHERE external_message_id = ?
                        """,
                Integer.class,
                runtimeEvidence.eventUid()));

        JsonNode organizationView = data(read(
                principal,
                "/api/v1/web/organizations/" + organizationCode
                        + "/device-deployments/" + deploymentCode,
                200));
        assertEquals(deploymentCode,
                organizationView.path("deploymentCode").asText());

        JsonNode versionOne = data(read(
                principal,
                organizationDeploymentBase + "/" + deploymentCode
                        + "/configuration-versions/1",
                200));
        Map<String, Object> extremeDevice = new LinkedHashMap<>(
                objectMapper.convertValue(
                        versionOne.path("device"), Map.class));
        extremeDevice.put("edgeHeartbeatIntervalMs", 4294967295L);
        extremeDevice.put("edgeHeartbeatMissThreshold", 2147483647L);
        List<Map<String, Object>> extremePorts = new ArrayList<>();
        versionOne.path("ports").forEach(port -> extremePorts.add(
                new LinkedHashMap<>(
                        objectMapper.convertValue(port, Map.class))));
        JsonNode extremeConfiguration = data(write(
                principal,
                post(organizationDeploymentBase + "/" + deploymentCode
                        + "/configuration-releases"),
                UUID.randomUUID(),
                Map.of(
                        "expectedLatestVersion", 1,
                        "reason", "exercise maximum heartbeat window",
                        "locationCorrectionConfirmed", false,
                        "device", extremeDevice,
                        "ports", extremePorts),
                202));
        assertEquals(2, extremeConfiguration.path("versionNo").asLong());
        assertDoesNotThrow(taskGateReconciliation::reconcileAll);
        JsonNode extremeHeartbeatReadiness = data(read(
                platform,
                deploymentBase + "/" + deploymentCode
                        + "/acceptance-readiness",
                200));
        assertFalse(contains(
                extremeHeartbeatReadiness.path("blockers"),
                "TRUSTED_RUNTIME_STALE"));

        String audit = jdbc.queryForObject("""
                        SELECT CAST(JSON_ARRAYAGG(
                            safe_change_summary) AS CHAR)
                        FROM ops_audit_log
                        """, String.class);
        assertNotNull(audit);
        assertFalse(audit.contains("A区北门"));
        assertFalse(audit.contains("测试回收设备"));
    }

    private EdgeFactEvidence applyTrustedConfigurationProgress(
            String hardwareSn,
            String deploymentCode,
            String applicationUid) {
        Map<String, Object> target = jdbc.queryForMap("""
                SELECT
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
                  ON command_row.config_application_id =
                     application.id
                 AND command_row.command_type =
                     'APPLY_CONFIGURATION'
                WHERE application.application_uid = ?
                """, applicationUid);
        String commandUid = target.get("command_uid").toString();
        long version = ((Number) target.get("version_no")).longValue();
        String contentSha256 =
                target.get("content_sha256").toString();
        String mcuPayloadSha256 =
                target.get("mcu_payload_sha256").toString();
        String mcuCommandUid = UUID.randomUUID().toString();

        Map<String, Object> semanticPayload = new TreeMap<>();
        semanticPayload.put("applicationUid", applicationUid);
        semanticPayload.put("contentSha256", contentSha256);
        semanticPayload.put("errorCode", null);
        semanticPayload.put("mcuCommandUid", mcuCommandUid);
        semanticPayload.put(
                "mcuPayloadSha256", mcuPayloadSha256);
        semanticPayload.put("stage", "APPLIED");
        semanticPayload.put("version", version);
        String payloadSha256 = sha256Hex(
                objectMapper.writeValueAsBytes(semanticPayload));

        Map<String, Object> wireTarget = new LinkedHashMap<>();
        wireTarget.put("type", 1);
        wireTarget.put("uid", applicationUid);
        Map<String, Object> wire = new LinkedHashMap<>();
        wire.put("applicationUid", applicationUid);
        wire.put("clockQuality", 1);
        wire.put("commandUid", commandUid);
        wire.put("contentSha256", contentSha256);
        wire.put("deliveryClass", 1);
        wire.put("deploymentCode", deploymentCode);
        wire.put("edgeEventSequence", 1);
        wire.put("errorCode", "");
        wire.put("errorCodePresent", false);
        wire.put("eventType", 1);
        wire.put("eventUid", UUID.randomUUID().toString());
        wire.put("mcuCommandUid", mcuCommandUid);
        wire.put("mcuCommandUidPresent", true);
        wire.put("mcuPayloadSha256", mcuPayloadSha256);
        wire.put(
                "occurredAt",
                Instant.now().truncatedTo(
                        ChronoUnit.MILLIS).toString());
        wire.put("occurredAtPresent", true);
        wire.put("payloadSha256", payloadSha256);
        wire.put("schemaVersion", 1);
        wire.put("stage", 2);
        wire.put("target", wireTarget);
        wire.put("version", version);

        acceptTrustedWireEvent(
                "configurationProgress",
                wire,
                hardwareSn);
        return new EdgeFactEvidence(
                wire.get("eventUid").toString(),
                payloadSha256);
    }

    private EdgeFactEvidence applyBusinessConfirmationReceipt(
            String hardwareSn,
            String deploymentCode,
            JsonNode confirmationEnvelope,
            long edgeEventSequence) {
        JsonNode confirmation =
                confirmationEnvelope.path("payload");
        Map<String, Object> semanticPayload = new TreeMap<>();
        semanticPayload.put(
                "confirmationUid",
                confirmation.path("confirmationUid").asText());
        semanticPayload.put(
                "originalEventUid",
                confirmation.path("originalEventUid").asText());
        semanticPayload.put(
                "originalPayloadSha256",
                confirmation.path("originalPayloadSha256").asText());
        semanticPayload.put(
                "outcome",
                confirmation.path("outcome").asText());
        String payloadSha256 = canonicalizer.hex(
                canonicalizer.payloadSha256(semanticPayload));
        String eventUid = UUID.randomUUID().toString();

        Map<String, Object> target = new LinkedHashMap<>();
        target.put("type", 1);
        target.put(
                "uid",
                confirmation.path("confirmationUid").asText());
        Map<String, Object> wire = new LinkedHashMap<>();
        wire.put("clockQuality", 1);
        wire.put(
                "commandUid",
                confirmationEnvelope.path("commandUid").asText());
        wire.put(
                "confirmationUid",
                confirmation.path("confirmationUid").asText());
        wire.put("deliveryClass", 1);
        wire.put("deploymentCode", deploymentCode);
        wire.put("edgeEventSequence", edgeEventSequence);
        wire.put("eventType", 1);
        wire.put("eventUid", eventUid);
        wire.put(
                "occurredAt",
                Instant.now().truncatedTo(
                        ChronoUnit.MILLIS).toString());
        wire.put("occurredAtPresent", true);
        wire.put(
                "originalEventUid",
                confirmation.path("originalEventUid").asText());
        wire.put(
                "originalPayloadSha256",
                confirmation.path("originalPayloadSha256").asText());
        wire.put("outcome", 1);
        wire.put("payloadSha256", payloadSha256);
        wire.put("schemaVersion", 1);
        wire.put("target", target);

        acceptTrustedWireEvent(
                "businessConfirmationReceipt",
                wire,
                hardwareSn);
        return new EdgeFactEvidence(eventUid, payloadSha256);
    }

    private EdgeFactEvidence applyTrustedRuntimeSnapshot(
            String hardwareSn,
            String deploymentCode,
            String applicationUid,
            long edgeEventSequence) throws Exception {
        Map<String, Object> configuration = jdbc.queryForMap("""
                SELECT
                    version.version_no,
                    LOWER(HEX(version.content_sha256))
                        AS content_sha256,
                    LOWER(HEX(version.mcu_payload_sha256))
                        AS mcu_payload_sha256
                FROM dev_config_application application
                JOIN dev_config_version version
                  ON version.id = application.config_version_id
                WHERE application.application_uid = ?
                """, applicationUid);
        long version = ((Number) configuration.get(
                "version_no")).longValue();
        String contentSha256 =
                configuration.get("content_sha256").toString();
        String mcuPayloadSha256 =
                configuration.get("mcu_payload_sha256").toString();

        Map<String, Object> semanticEvent = objectMapper.readValue(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "device-runtime-snapshot.event.json")),
                Map.class);
        Map<String, Object> semanticPayload =
                mutableMap(semanticEvent.get("payload"));
        Map<String, Object> semanticConfig =
                mutableMap(semanticPayload.get("appliedConfig"));
        semanticConfig.put("version", version);
        semanticConfig.put("contentSha256", contentSha256);
        semanticConfig.put(
                "mcuPayloadSha256", mcuPayloadSha256);
        semanticPayload.put("pendingReliableEventCount", 0);
        for (Object item : (List<?>) semanticPayload.get("ports")) {
            Map<String, Object> port = (Map<String, Object>) item;
            port.put("fullnessSensorKind", "DIGITAL_INFRARED");
            port.put("fullnessSensorValue", "CLEAR");
            port.put("fullnessSampleBasis", "NOT_SAMPLED");
            port.put("representativeDistanceMm", null);
            port.put("fullnessValidSampleCount", 1);
        }
        String payloadSha256 = canonicalizer.hex(
                canonicalizer.payloadSha256(semanticPayload));

        Map<String, Object> fixture = objectMapper.readValue(
                Files.readString(contractPath(
                        "contracts/examples/onenet-wire/"
                                + "device-runtime-snapshot.event-wire.json")),
                Map.class);
        Map<String, Object> oneJson =
                mutableMap(fixture.get("oneJsonPayload"));
        Map<String, Object> params =
                mutableMap(oneJson.get("params"));
        Map<String, Object> eventWrapper =
                mutableMap(params.get("deviceRuntimeSnapshot"));
        Map<String, Object> wire =
                mutableMap(eventWrapper.get("value"));
        Map<String, Object> wireConfig =
                mutableMap(wire.get("appliedConfig"));
        wireConfig.put("version", version);
        wireConfig.put("contentSha256", contentSha256);
        wireConfig.put("mcuPayloadSha256", mcuPayloadSha256);
        wire.put("deploymentCode", deploymentCode);
        wire.put("edgeEventSequence", edgeEventSequence);
        String eventUid = UUID.randomUUID().toString();
        wire.put("eventUid", eventUid);
        wire.put(
                "occurredAt",
                Instant.now().truncatedTo(
                        ChronoUnit.MILLIS).toString());
        wire.put("pendingReliableEventCount", 0);
        for (Object item : (List<?>) wire.get("ports")) {
            Map<String, Object> port = (Map<String, Object>) item;
            port.put("fullnessSensorKind", 2);
            port.put("fullnessSensorValue", 1);
            port.put("fullnessSampleBasis", 4);
            port.put("representativeDistanceMmPresent", false);
            port.remove("representativeDistanceMm");
            port.put("fullnessValidSampleCount", 1);
        }
        wire.put("payloadSha256", payloadSha256);
        mutableMap(wire.get("target")).put(
                "uid", deploymentCode);

        acceptTrustedWireEvent(
                "deviceRuntimeSnapshot",
                wire,
                hardwareSn);
        return new EdgeFactEvidence(eventUid, payloadSha256);
    }

    private EdgeFactEvidence applyTrustedFaultFact(
            String identifier,
            String semanticFixture,
            String wireFixture,
            String hardwareSn,
            String deploymentCode,
            String faultUid,
            long edgeEventSequence,
            long mcuEventSequence) throws Exception {
        Map<String, Object> semanticEvent = objectMapper.readValue(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + semanticFixture)),
                Map.class);
        Map<String, Object> semanticPayload =
                mutableMap(semanticEvent.get("payload"));
        semanticPayload.put("faultUid", faultUid);
        semanticPayload.put(
                "mcuEventSequence", mcuEventSequence);
        String payloadSha256 = canonicalizer.hex(
                canonicalizer.payloadSha256(semanticPayload));

        Map<String, Object> fixture = objectMapper.readValue(
                Files.readString(contractPath(
                        "contracts/examples/onenet-wire/"
                                + wireFixture)),
                Map.class);
        Map<String, Object> oneJson =
                mutableMap(fixture.get("oneJsonPayload"));
        Map<String, Object> params =
                mutableMap(oneJson.get("params"));
        Map<String, Object> eventWrapper =
                mutableMap(params.get(identifier));
        Map<String, Object> wire =
                mutableMap(eventWrapper.get("value"));
        String eventUid = UUID.randomUUID().toString();
        wire.put("deploymentCode", deploymentCode);
        wire.put("edgeEventSequence", edgeEventSequence);
        wire.put("eventUid", eventUid);
        wire.put("faultUid", faultUid);
        wire.put("mcuEventSequence", mcuEventSequence);
        wire.put(
                "occurredAt",
                Instant.now().truncatedTo(
                        ChronoUnit.MILLIS).toString());
        wire.put("payloadSha256", payloadSha256);
        mutableMap(wire.get("target")).put(
                "uid", deploymentCode);

        acceptTrustedWireEvent(identifier, wire, hardwareSn);
        return new EdgeFactEvidence(eventUid, payloadSha256);
    }

    private EdgeFactEvidence applyTrustedSafetyState(
            String hardwareSn,
            String deploymentCode,
            String smokeState,
            long edgeEventSequence,
            long mcuEventSequence) throws Exception {
        Map<String, Object> semanticEvent = objectMapper.readValue(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "safety-sensor-state-changed"
                                + ".event.json")),
                Map.class);
        Map<String, Object> semanticPayload =
                mutableMap(semanticEvent.get("payload"));
        semanticPayload.put(
                "mcuEventSequence", mcuEventSequence);
        semanticPayload.put("smokeState", smokeState);
        semanticPayload.put("workType", "NONE");
        semanticPayload.put("workUid", null);
        String payloadSha256 = canonicalizer.hex(
                canonicalizer.payloadSha256(semanticPayload));

        Map<String, Object> fixture = objectMapper.readValue(
                Files.readString(contractPath(
                        "contracts/examples/onenet-wire/"
                                + "safety-sensor-state-changed"
                                + ".event-wire.json")),
                Map.class);
        Map<String, Object> oneJson =
                mutableMap(fixture.get("oneJsonPayload"));
        Map<String, Object> params =
                mutableMap(oneJson.get("params"));
        Map<String, Object> eventWrapper =
                mutableMap(params.get(
                        "safetySensorStateChanged"));
        Map<String, Object> wire =
                mutableMap(eventWrapper.get("value"));
        String eventUid = UUID.randomUUID().toString();
        wire.put("deploymentCode", deploymentCode);
        wire.put("edgeEventSequence", edgeEventSequence);
        wire.put("eventUid", eventUid);
        wire.put("mcuEventSequence", mcuEventSequence);
        wire.put("smokeState",
                "ALARM".equals(smokeState) ? 2 : 1);
        wire.put("workType", 1);
        wire.put("workUidPresent", false);
        wire.remove("workUid");
        wire.put(
                "occurredAt",
                Instant.now().truncatedTo(
                        ChronoUnit.MILLIS).toString());
        wire.put("payloadSha256", payloadSha256);
        mutableMap(wire.get("target")).put(
                "uid", deploymentCode);

        acceptTrustedWireEvent(
                "safetySensorStateChanged",
                wire,
                hardwareSn);
        return new EdgeFactEvidence(eventUid, payloadSha256);
    }

    private void assertPendingConfirmation(
            EdgeFactEvidence evidence) {
        assertEquals(
                "PENDING",
                jdbc.queryForObject("""
                                SELECT state
                                FROM ops_reliable_task
                                WHERE task_key = ?
                                  AND task_type =
                                      'CONFIRM_EDGE_EVENT'
                                  AND source_device_command_id IS NULL
                                """,
                        String.class,
                        "CONFIRM_EDGE_EVENT:"
                                + evidence.eventUid().toUpperCase()));
    }

    private List<ReliableWorkerBatchResult> runConcurrentDeviceBatches(
            String workerPrefix) throws Exception {
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            List<Future<ReliableWorkerBatchResult>> futures = List.of(
                    executor.submit(() -> {
                        ready.countDown();
                        start.await();
                        return worker.runBatch(workerPrefix + "-1");
                    }),
                    executor.submit(() -> {
                        ready.countDown();
                        start.await();
                        return worker.runBatch(workerPrefix + "-2");
                    }));
            assertTrue(ready.await(5, TimeUnit.SECONDS));
            start.countDown();
            return List.of(
                    futures.get(0).get(30, TimeUnit.SECONDS),
                    futures.get(1).get(30, TimeUnit.SECONDS));
        } finally {
            executor.shutdownNow();
            assertTrue(executor.awaitTermination(5, TimeUnit.SECONDS));
        }
    }

    private void acceptTrustedWireEvent(
            String identifier,
            Map<String, Object> wire,
            String hardwareSn) {
        Map<String, Object> wrapped = Map.of("value", wire);
        Map<String, Object> params =
                Map.of(identifier, wrapped);
        Map<String, Object> subData = new LinkedHashMap<>();
        subData.put("productId", "device-integration-product");
        subData.put("deviceName", hardwareSn);
        subData.put("params", params);
        Map<String, Object> decrypted = new LinkedHashMap<>();
        decrypted.put("msgType", "thingEvent");
        decrypted.put("subData", subData);

        OneNetProperties properties = new OneNetProperties();
        properties.setProductId("device-integration-product");
        CosProperties cosProperties = new CosProperties();
        cosProperties.setBaseUrl(
                "https://ecobin-contract-1250000000"
                        + ".cos.ap-guangzhou.myqcloud.com");
        OneNetEventDispatcher dispatcher =
                new OneNetEventDispatcher(
                        trustedInboxPort,
                        sourceScopePort,
                        properties,
                        cosProperties,
                        objectMapper);
        String eventUid = wire.get("eventUid").toString();
        dispatcher.handle(
                objectMapper.writeValueAsString(decrypted),
                "device-integration-message-" + eventUid,
                "encrypted-device-integration-envelope"
                        .getBytes(StandardCharsets.UTF_8));
        boolean runtimeTelemetry =
                "deviceRuntimeSnapshot".equals(identifier);
        assertEquals(
                runtimeTelemetry
                        ? "ORGANIZATION|PROCESSED"
                        : "ORGANIZATION|RECEIVED",
                jdbc.queryForObject("""
                        SELECT CONCAT(scope_kind, '|', processing_state)
                        FROM ops_inbox_message
                        WHERE external_message_id = ?
                        """,
                String.class,
                eventUid));

        if (runtimeTelemetry) {
            assertEquals(0, jdbc.queryForObject("""
                            SELECT COUNT(*)
                            FROM ops_reliable_task task
                            JOIN ops_inbox_message inbox
                              ON inbox.id = task.source_inbox_id
                            WHERE inbox.external_message_id = ?
                            """,
                    Integer.class,
                    eventUid));
            return;
        }

        ReliableWorkerBatchResult result = inboxWorker.runBatch(
                "device-inbox-" + eventUid);
        assertEquals(1, result.claimed());
        assertEquals(
                0,
                result.failed(),
                () -> inboxFailureDiagnostic(eventUid));
        assertEquals(1, result.accepted());
        assertEquals("PROCESSED", jdbc.queryForObject("""
                        SELECT processing_state
                        FROM ops_inbox_message
                        WHERE external_message_id = ?
                        """,
                String.class,
                eventUid));
    }

    private void acceptTransportLifecycle(
            String hardwareSn,
            String status,
            long observedAtEpochMillis,
            String externalMessageId) {
        Map<String, Object> subData = new LinkedHashMap<>();
        subData.put("productId", "device-integration-product");
        subData.put("deviceName", hardwareSn);
        subData.put("time", observedAtEpochMillis);
        Map<String, Object> decrypted = new LinkedHashMap<>();
        decrypted.put(
                "msgType",
                "ONLINE".equals(status)
                        ? "deviceOnline"
                        : "deviceOffline");
        decrypted.put("subData", subData);

        OneNetProperties properties = new OneNetProperties();
        properties.setProductId("device-integration-product");
        CosProperties cosProperties = new CosProperties();
        OneNetEventDispatcher dispatcher = new OneNetEventDispatcher(
                trustedInboxPort,
                sourceScopePort,
                properties,
                cosProperties,
                objectMapper);
        dispatcher.handle(
                objectMapper.writeValueAsString(decrypted),
                externalMessageId,
                "encrypted-device-lifecycle-envelope"
                        .getBytes(StandardCharsets.UTF_8));
        assertEquals("PLATFORM|RECEIVED", jdbc.queryForObject("""
                        SELECT CONCAT(scope_kind, '|', processing_state)
                        FROM ops_inbox_message
                        WHERE external_message_id = ?
                        """, String.class, externalMessageId));

        ReliableWorkerBatchResult result = inboxWorker.runBatch(
                "device-lifecycle-" + externalMessageId);
        assertEquals(1, result.claimed());
        assertEquals(1, result.accepted());
        assertEquals(0, result.failed());
        assertEquals("PROCESSED", jdbc.queryForObject("""
                        SELECT processing_state
                        FROM ops_inbox_message
                        WHERE external_message_id = ?
                        """, String.class, externalMessageId));
    }

    private String inboxFailureDiagnostic(String eventUid) {
        return jdbc.queryForObject("""
                        SELECT attempt.redacted_diagnostic
                        FROM ops_task_attempt attempt
                        JOIN ops_reliable_task task
                          ON task.id = attempt.task_id
                        JOIN ops_inbox_message inbox
                          ON inbox.id = task.source_inbox_id
                        WHERE inbox.external_message_id = ?
                        ORDER BY attempt.id DESC
                        LIMIT 1
                        """,
                String.class,
                eventUid);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> mutableMap(Object value) {
        return (Map<String, Object>) value;
    }

    private static Path contractPath(String relative) {
        Path workingDirectory = Path.of("")
                .toAbsolutePath()
                .normalize();
        Path repository = Files.isDirectory(
                workingDirectory.resolve("contracts"))
                ? workingDirectory
                : workingDirectory.getParent();
        return repository.resolve(relative).normalize();
    }

    private static String sha256Hex(byte[] value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256")
                            .digest(value));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private record EdgeFactEvidence(
            String eventUid,
            String payloadSha256) {
    }

    private void createEnabledScope(
            BrowserClient platform,
            String tenantCode,
            String organizationCode,
            String principalLogin) throws Exception {
        data(write(
                platform,
                post("/api/v1/web/platform/tenants"),
                UUID.randomUUID(),
                Map.of(
                        "tenantCode", tenantCode,
                        "enterpriseName", "Device test tenant"),
                201));
        write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/principal-account"),
                UUID.randomUUID(),
                Map.of(
                        "loginName", principalLogin,
                        "initialPassword", PRINCIPAL_PASSWORD,
                        "displayName", "Device principal",
                        "expectedVersion", 0),
                201);
        long tenantVersion = data(read(
                platform,
                "/api/v1/web/platform/tenants/" + tenantCode,
                200)).path("version").asLong();
        write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/activations"),
                UUID.randomUUID(),
                Map.of("expectedVersion", tenantVersion),
                200);
        JsonNode organization = data(write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/organizations"),
                UUID.randomUUID(),
                Map.of(
                        "organizationCode", organizationCode,
                        "organizationName", "Device test organization"),
                201));
        write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/organizations/" + organizationCode
                        + "/activations"),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion",
                        organization.path("version").asLong()),
                200);
    }

    private MvcResult login(
            BrowserClient client,
            String path,
            String loginName,
            String password,
            int expectedStatus) throws Exception {
        return write(
                client,
                post(path),
                null,
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
        assertEquals(
                expectedStatus,
                result.getResponse().getStatus(),
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
        assertEquals(
                expectedStatus,
                result.getResponse().getStatus(),
                result.getResponse().getContentAsString());
        return result;
    }

    private String csrf(BrowserClient client) throws Exception {
        MvcResult result = mockMvc.perform(withCookies(
                        get("/api/v1/web/auth/csrf-token"),
                        client))
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
        return cookies.length == 0
                ? request
                : request.cookie(cookies);
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

    private static boolean contains(JsonNode array, String value) {
        for (JsonNode item : array) {
            if (value.equals(item.asString())) {
                return true;
            }
        }
        return false;
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
                            ? "ONENET_10421"
                            : null,
                    outcome == DeviceCommandSubmissionResult.Outcome
                            .TARGET_OFFLINE
                            ? "isolated test target is offline"
                            : "isolated test platform accepted; device proof pending");
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
            java.util.Arrays.fill(digest, fill);
            return digest;
        }
    }

    private static final class BrowserClient {

        private final Map<String, Cookie> cookies =
                new LinkedHashMap<>();

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
