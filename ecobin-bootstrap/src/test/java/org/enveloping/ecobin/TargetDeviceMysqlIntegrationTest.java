package org.enveloping.ecobin;

import jakarta.servlet.http.Cookie;
import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
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

import java.util.Arrays;
import java.util.LinkedHashMap;
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
        "ecobin.development.default-platform-admin.enabled=false",
        "ecobin.funds.wechat-pay.merchant-profile-registration-enabled=false",
        "onenet.subscription.enabled=false",
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
        createAndEnableTenant(
                platform,
                otherTenantCode,
                "device-other-principal-" + run);

        BrowserClient principal = new BrowserClient();
        login(principal, "/api/v1/web/auth/sessions",
                principalLogin, PRINCIPAL_PASSWORD, 201);

        String hardwareSn = "HW-PERMANENT-" + run;
        String firstBag = "BAG_A_" + run;
        String secondBag = "BAG_B_" + run;
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
        assertEquals("NOT_ASSIGNED", created.path("miniappQrStatus").asText());
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
        assertEquals("PENDING",
                organizationAssigned.path("miniappQrStatus").asText());
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

    private void seedAcceptedEvidenceFixture(String hardwareSn) {
        long assetId = assetId(hardwareSn);
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
                            1, 1, 1, 1, 1, 1, 1, 1, 0, 0,
                            'PASSED', JSON_ARRAY(), JSON_OBJECT(
                                'verifiedPortCount', 2,
                                'verifiedCameraCount', 2
                            ), UNHEX(SHA2(?, 256)),
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3),
                            UTC_TIMESTAMP(3)
                        )
                        """,
                evidenceUid, assetId, challengeUid, commandUid, storeUid,
                digestSeed);
        jdbc.update("""
                        UPDATE dev_device_asset
                        SET acceptance_status = 'PASSED',
                            accepted_at = UTC_TIMESTAMP(3),
                            acceptance_evidence_sha256 =
                                UNHEX(SHA2(?, 256)),
                            last_acceptance_evaluated_at = UTC_TIMESTAMP(3),
                            acceptance_failure_json = NULL,
                            control_version = control_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE id = ?
                        """, digestSeed, assetId);
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
