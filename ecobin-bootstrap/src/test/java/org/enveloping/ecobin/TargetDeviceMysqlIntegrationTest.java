package org.enveloping.ecobin;

import jakarta.servlet.http.Cookie;
import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetEventDispatcher;
import org.enveloping.ecobin.integration.onenet.outbound.OneNetProperties;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
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
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
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
        "onenet.subscription.enabled=false",
        "jwt.secret=DEVICE_TEST_SECRET_MUST_BE_AT_LEAST_32_BYTES_LONG",
        "app.crypto.aes-key=device_integration_test_aes_key"
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
    private AcceptedSubmissionProbe submissionProbe;

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

        String deploymentBase = "/api/v1/web/platform/tenants/"
                + tenantCode + "/organizations/" + organizationCode
                + "/device-deployments";
        JsonNode deployment = data(write(
                platform,
                post(deploymentBase),
                UUID.randomUUID(),
                Map.of(
                        "hardwareSn", hardwareSn,
                        "expectedAssetVersion", 0),
                201));
        String deploymentCode =
                deployment.path("deploymentCode").asText();
        assertFalse(deploymentCode.isBlank());
        assertEquals(
                "COMMISSIONING",
                deployment.path("lifecycleStatus").asText());
        assertEquals(2, deployment.path("portCount").asInt());
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

        JsonNode accepted = data(write(
                platform,
                post(deploymentBase + "/" + deploymentCode
                        + "/configuration-releases"),
                UUID.randomUUID(),
                configurationBody(),
                202));
        String applicationUid =
                accepted.path("applicationUid").asText();
        assertEquals("PENDING", accepted.path("status").asText());
        assertEquals("PENDING", accepted.path("dispatchState").asText());
        assertEquals(1, accepted.path("versionNo").asLong());

        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_config_version version
                        JOIN dev_config_application application
                          ON application.config_version_id = version.id
                        WHERE application.application_uid = ?
                          AND version.version_no = 1
                        """, Integer.class, applicationUid));
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

        MvcResult activation = write(
                platform,
                post(deploymentBase + "/" + deploymentCode
                        + "/activations"),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion", 0,
                        "expectedConfigurationVersion", 1,
                        "acceptanceConfirmed", true,
                        "reason", "isolated integration verification"),
                422);
        JsonNode problem = json(activation);
        assertEquals(
                "DEVICE.DEPLOYMENT_NOT_ACTIVATABLE",
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

        BrowserClient principal = new BrowserClient();
        login(
                principal,
                "/api/v1/web/auth/sessions",
                principalLogin,
                PRINCIPAL_PASSWORD,
                201);
        JsonNode organizationView = data(read(
                principal,
                "/api/v1/web/organizations/" + organizationCode
                        + "/device-deployments/" + deploymentCode,
                200));
        assertEquals(deploymentCode,
                organizationView.path("deploymentCode").asText());

        String audit = jdbc.queryForObject("""
                        SELECT CAST(JSON_ARRAYAGG(
                            safe_change_summary) AS CHAR)
                        FROM ops_audit_log
                        """, String.class);
        assertNotNull(audit);
        assertFalse(audit.contains("A区北门"));
        assertFalse(audit.contains("测试回收设备"));
    }

    private void applyTrustedConfigurationProgress(
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

        Map<String, Object> wrapped = Map.of("value", wire);
        Map<String, Object> params =
                Map.of("configurationProgress", wrapped);
        Map<String, Object> subData = new LinkedHashMap<>();
        subData.put("productId", "device-integration-product");
        subData.put("deviceName", hardwareSn);
        subData.put("params", params);
        Map<String, Object> decrypted = new LinkedHashMap<>();
        decrypted.put("msgType", "thingEvent");
        decrypted.put("subData", subData);

        OneNetProperties properties = new OneNetProperties();
        properties.setProductId("device-integration-product");
        OneNetEventDispatcher dispatcher =
                new OneNetEventDispatcher(
                        trustedInboxPort,
                        sourceScopePort,
                        properties,
                        objectMapper);
        dispatcher.handle(
                objectMapper.writeValueAsString(decrypted),
                "device-integration-message",
                "encrypted-device-integration-envelope"
                        .getBytes(StandardCharsets.UTF_8));
        assertEquals("ORGANIZATION|RECEIVED", jdbc.queryForObject("""
                        SELECT CONCAT(scope_kind, '|', processing_state)
                        FROM ops_inbox_message
                        WHERE external_message_id = ?
                        """,
                String.class,
                wire.get("eventUid").toString()));

        ReliableWorkerBatchResult result = inboxWorker.runBatch(
                "device-inbox-integration-worker");
        assertEquals(1, result.claimed());
        assertEquals(1, result.accepted());
        assertEquals(0, result.failed());
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

    private Map<String, Object> configurationBody() {
        Map<String, Object> device = new LinkedHashMap<>();
        device.put("displayName", "测试回收设备");
        device.put("address", "A区北门");
        device.put("longitude", "113.9345000");
        device.put("latitude", "22.5401000");
        device.put("edgeHeartbeatIntervalMs", 30_000);
        device.put("edgeHeartbeatMissThreshold", 3);
        device.put("mcuHeartbeatIntervalMs", 5_000);
        device.put("mcuHeartbeatMissThreshold", 3);
        device.put("doorCloseRetryLimit", 3);
        device.put("continueDeliveryWaitMs", 30_000);
        device.put("negativeWeightThresholdGram", 500);
        device.put("deliveryAutoCloseMs", 120_000);
        device.put("weightMeasurementTimeoutMs", 6_000);
        device.put("deliveryDoorTravelWaitMs", 30_000);
        device.put("cleanSolenoidPulseMs", 1_000);
        device.put("smokeMonitoringEnabled", true);

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("expectedLatestVersion", 0);
        body.put("reason", "initial complete configuration");
        body.put("locationCorrectionConfirmed", false);
        body.put("device", device);
        body.put("ports", List.of(port(1, "0.4501"), port(2, "0.4502")));
        return body;
    }

    private static Map<String, Object> port(
            int portNo, String unitPrice) {
        Map<String, Object> port = new LinkedHashMap<>();
        port.put("portNo", portNo);
        port.put("displayName", "投口" + portNo);
        port.put("enabled", true);
        port.put("unitPriceYuanPerKg", unitPrice);
        port.put("fullnessMode", "INFRARED_OR_WEIGHT");
        port.put("fullnessWeightKg", "50.000");
        port.put("deliverySettleDelayMs", 3_000);
        port.put("fullnessInitialDelayMs", 5_000);
        port.put("fullnessRecheckDelayMs", 10_000);
        port.put("doorAutoCloseTimeoutMs", 60_000);
        port.put("fullnessSensorKind", "ULTRASONIC");
        port.put("fullnessDistanceThresholdMm", 600);
        port.put("fullnessSampleCount", 5);
        port.put("fullnessMinimumValidSampleCount", 3);
        port.put("fullnessEchoTimeoutUs", 30_000);
        port.put("weightStableWindowMs", 1_500);
        port.put("weightMaximumFluctuationGram", 20);
        port.put("weightRequiredSampleCount", 10);
        port.put("weightMeasurementTimeoutMs", 6_000);
        port.put("weightMinimumGram", -5_000);
        port.put("weightMaximumGram", 100_000);
        port.put("calibrationVersion", 4);
        port.put("infraredSampleTimeoutMs", 3_000);
        port.put("deliveryDoorOperationTimeoutMs", 60_000);
        return port;
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

        @Override
        public DeviceCommandSubmissionResult submit(
                DeviceCommandSubmission submission) {
            submissionCount++;
            lastSubmission = submission;
            return new DeviceCommandSubmissionResult(
                    DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                    digest((byte) 1),
                    digest((byte) 2),
                    200,
                    null,
                    "isolated test platform accepted; device proof pending");
        }

        int submissionCount() {
            return submissionCount;
        }

        DeviceCommandSubmission lastSubmission() {
            return lastSubmission;
        }

        void reset() {
            submissionCount = 0;
            lastSubmission = null;
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
