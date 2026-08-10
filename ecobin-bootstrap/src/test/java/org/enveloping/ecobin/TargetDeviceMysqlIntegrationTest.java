package org.enveloping.ecobin;

import jakarta.servlet.http.Cookie;
import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxExecutionLane;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceiptState;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceInboxWorkerPort;
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
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

/**
 * MySQL proof for the V36 permanent device model.
 *
 * <p>The test deliberately has no deployment, pool, reclaim or manual
 * activation fixture. It proves that the asset is the only lifecycle root,
 * ownership is written once, organization assignment creates the unattended
 * initial configuration, and disabled or retired assets disappear from
 * tenant-facing reads.</p>
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
        "ecobin.miniapp.device-entry-base-url=https://example.test/ecobin/device",
        "ecobin.development.default-platform-admin.enabled=false",
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
    void permanentOwnershipAutomaticallyCreatesOrganizationFactsAndHidesStoppedAssets()
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
                        "expectedPortCount", 2,
                        "factoryBags", java.util.List.of(
                                Map.of("portNo", 1, "bagCode", firstBag),
                                Map.of("portNo", 2, "bagCode", secondBag))),
                201));
        String deviceCode = created.path("deviceCode").asText();
        assertTrue(deviceCode.matches("Dv_[A-Za-z0-9_-]{24,61}"));
        assertEquals("NORMAL", created.path("lifecycleStatus").asText());
        assertEquals("PENDING", created.path("acceptanceStatus").asText());
        assertTrue(created.path("deviceEntryUrl").isNull());
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

        long assetId = assetId(hardwareSn);
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
        read(principal, "/api/v1/web/device-assets/" + hardwareSn, 404);
        assertEquals(0, data(read(principal,
                "/api/v1/web/device-assets?page=1&pageSize=20",
                200)).path("items").size());

        JsonNode restored = data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/restorations"),
                UUID.randomUUID(),
                Map.of("expectedVersion", 4, "reason", "检查完成"),
                200));
        assertEquals("NORMAL", restored.path("lifecycleStatus").asText());
        read(principal, "/api/v1/web/device-assets/" + hardwareSn, 200);

        JsonNode retired = data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/retirements"),
                UUID.randomUUID(),
                Map.of("expectedVersion", 5, "reason", "永久报废"),
                200));
        assertEquals("RETIRED", retired.path("lifecycleStatus").asText());
        read(principal, "/api/v1/web/device-assets/" + hardwareSn, 404);
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
                        "expectedPortCount", 1,
                        "factoryBags", List.of(Map.of(
                                "portNo", 1,
                                "bagCode", factoryBagCode))),
                201));
        seedAcceptedEvidenceFixture(hardwareSn);
        data(write(
                platform,
                post("/api/v1/web/platform/device-assets/" + hardwareSn
                        + "/acceptance-evaluations"),
                UUID.randomUUID(),
                Map.of(),
                200));
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

    private void seedAcceptedEvidenceFixture(String hardwareSn) {
        long assetId = assetId(hardwareSn);
        int expectedPortCount = jdbc.queryForObject("""
                        SELECT expected_port_count
                        FROM dev_device_asset
                        WHERE id = ?
                        """,
                Integer.class,
                assetId);
        String evidenceUid = UUID.randomUUID().toString();
        String challengeUid = UUID.randomUUID().toString();
        String commandUid = UUID.randomUUID().toString();
        String storeUid = UUID.randomUUID().toString();
        String digestSeed = "accepted:" + hardwareSn;
        jdbc.update("""
                        INSERT INTO dev_device_acceptance_evidence (
                            evidence_uid, asset_id, challenge_uid, command_uid,
                            evidence_schema_version, edge_store_instance_uid,
                            edge_software_version, edge_protocol_version,
                            mcu_firmware_version, onenet_online,
                            persistent_store_healthy, trusted_time_healthy,
                            configuration_persistence_healthy,
                            mcu_communication_healthy, sensors_healthy,
                            cameras_capture_healthy, camera_upload_healthy,
                            mcu_simulated, cameras_simulated,
                            evaluation_status, failure_reasons_json,
                            evidence_json, evidence_sha256,
                            observed_at, received_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, 1, ?, '0.1.0', '2', 'mcu-real',
                            1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
                            'PASSED', JSON_ARRAY(), JSON_OBJECT(
                                'verifiedPortCount', ?,
                                'verifiedCameraCount', ?
                            ), UNHEX(SHA2(?, 256)),
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3),
                            UTC_TIMESTAMP(3)
                        )
                        """,
                evidenceUid, assetId, challengeUid, commandUid, storeUid,
                expectedPortCount, expectedPortCount, digestSeed);
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
