package org.enveloping.ecobin;

import jakarta.servlet.http.Cookie;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort;
import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort;
import org.enveloping.ecobin.funds.application.recharge.RechargeApplicationService;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.time.LocalDateTime;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;

@SpringBootTest(properties = {
        "spring.datasource.url=${ECOBIN_V01_MYSQL_URL}",
        "spring.datasource.username=${ECOBIN_V01_MYSQL_USERNAME}",
        "spring.datasource.password=${ECOBIN_V01_MYSQL_PASSWORD}",
        "spring.sql.init.mode=never",
        "ecobin.database.epoch.test-bypass=false",
        "ecobin.external.mode=fake",
        "ecobin.external.fake.block-inbound=true",
        "onenet.subscription.enabled=false",
        "jwt.secret=IDENTITY_TEST_SECRET_MUST_BE_AT_LEAST_32_BYTES_LONG"
})
@AutoConfigureMockMvc
@EnabledIfEnvironmentVariable(
        named = "ECOBIN_V01_MYSQL_URL",
        matches = "jdbc:mysql:.+")
class TargetWebIdentityMysqlIntegrationTest {

    private static final String PLATFORM_PASSWORD = "PlatformPass123!";
    private static final String PRINCIPAL_PASSWORD = "PrincipalPass123!";
    private static final String WORKER_PASSWORD = "WorkerPass123!";

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JdbcTemplate jdbc;

    @Autowired
    private PasswordEncoder passwordEncoder;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private ReliableFundsTaskRegistrationPort reliableFundsTasks;

    @Autowired
    private PlatformTransactionManager transactionManager;

    @Autowired
    private FundsOperationalControlPort fundsOperationalControl;

    private String run;
    private String platformLogin;

    @BeforeEach
    void seedPlatformAdministrator() {
        run = Long.toUnsignedString(System.nanoTime(), 36);
        platformLogin = "v01-platform-" + run;
        jdbc.update("""
                        INSERT INTO iam_platform_admin (
                            platform_admin_uid, login_name, password_hash,
                            display_name, enabled, failed_login_count,
                            locked_until, auth_version, password_changed_at,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 'V01 integration administrator', 1, 0,
                            NULL, 0, UTC_TIMESTAMP(3), 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                UUID.randomUUID().toString(),
                platformLogin,
                passwordEncoder.encode(PLATFORM_PASSWORD));
    }

    @Test
    void targetWebIdentityClosesTenantOrganizationStaffAndSessionBoundaries()
            throws Exception {
        BrowserClient platform = new BrowserClient();
        MvcResult platformLoginResult = login(
                platform,
                "/api/v1/web/platform/auth/sessions",
                platformLogin,
                PLATFORM_PASSWORD,
                201);
        String loginSetCookie = platformLoginResult.getResponse()
                .getHeader("Set-Cookie");
        assertNotNull(loginSetCookie);
        assertTrue(loginSetCookie.contains("Secure"));
        assertTrue(loginSetCookie.contains("HttpOnly"));
        assertTrue(loginSetCookie.contains("SameSite=Lax"));
        assertTrue(loginSetCookie.contains("Path=/"));
        assertFalse(loginSetCookie.toLowerCase().contains("domain="));
        assertFalse(json(platformLoginResult).toString().contains("token"));

        String tenantA = code("ta");
        String tenantB = code("tb");
        UUID createTenantA = UUID.randomUUID();
        MvcResult tenantAResult = write(
                platform,
                post("/api/v1/web/platform/tenants"),
                createTenantA,
                Map.of(
                        "tenantCode", tenantA,
                        "enterpriseName", "Tenant A"),
                201);
        assertEquals("DISABLED", data(tenantAResult).path("status").asText());
        write(
                platform,
                post("/api/v1/web/platform/tenants"),
                createTenantA,
                Map.of(
                        "tenantCode", tenantA,
                        "enterpriseName", "Tenant A"),
                201);
        assertEquals(1, countTenant(tenantA));
        MvcResult idempotencyConflict = write(
                platform,
                post("/api/v1/web/platform/tenants"),
                createTenantA,
                Map.of(
                        "tenantCode", tenantA,
                        "enterpriseName", "Changed replay"),
                409);
        assertEquals(
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                json(idempotencyConflict).path("code").asText());
        write(
                platform,
                post("/api/v1/web/platform/tenants"),
                UUID.randomUUID(),
                Map.of(
                        "tenantCode", tenantB,
                        "enterpriseName", "Tenant B"),
                201);

        String principalLoginA = "v01-principal-" + run;
        String principalLoginB = "v01-principal-b-" + run;
        JsonNode principalA = data(write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantA
                        + "/principal-account"),
                UUID.randomUUID(),
                Map.of(
                        "loginName", principalLoginA,
                        "initialPassword", PRINCIPAL_PASSWORD,
                        "displayName", "Principal A",
                        "expectedVersion", 0),
                201));
        assertEquals(
                "TENANT_PRINCIPAL",
                principalA.path("accountKind").asText());

        MvcResult duplicateLogin = write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantB
                        + "/principal-account"),
                UUID.randomUUID(),
                Map.of(
                        "loginName", principalLoginA,
                        "initialPassword", PRINCIPAL_PASSWORD,
                        "displayName", "Duplicate",
                        "expectedVersion", 0),
                409);
        assertEquals(
                "IDENTITY.LOGIN_NAME_ALREADY_USED",
                json(duplicateLogin).path("code").asText());
        write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantB
                        + "/principal-account"),
                UUID.randomUUID(),
                Map.of(
                        "loginName", principalLoginB,
                        "initialPassword", PRINCIPAL_PASSWORD,
                        "displayName", "Principal B",
                        "expectedVersion", 0),
                201);

        activateTenant(platform, tenantA);
        activateTenant(platform, tenantB);

        BrowserClient oldPlatform = platform.copy();
        login(
                platform,
                "/api/v1/web/auth/sessions",
                principalLoginA,
                PRINCIPAL_PASSWORD,
                201);
        read(
                oldPlatform,
                "/api/v1/web/platform/auth/sessions/current",
                401);
        read(platform, "/api/v1/web/auth/sessions/current", 200);

        BrowserClient platformManager = new BrowserClient();
        login(
                platformManager,
                "/api/v1/web/platform/auth/sessions",
                platformLogin,
                PLATFORM_PASSWORD,
                201);
        JsonNode payoutGate = data(read(
                platformManager,
                "/api/v1/web/platform/payout-gate",
                200));
        assertEquals("OPEN", payoutGate.path("status").asText());
        assertTrue(payoutGate.path("version").asLong() >= 0);
        String organizationA = code("oa");
        String organizationB = code("ob");
        String otherTenantOrganization = code("ox");
        createAndActivateOrganization(
                platformManager, tenantA, organizationA, "Organization A");
        createAndActivateOrganization(
                platformManager, tenantA, organizationB, "Organization B");
        createAndActivateOrganization(
                platformManager,
                tenantB,
                otherTenantOrganization,
                "Other tenant organization");
        assertDefaultDeliveryRule(tenantA, organizationA);
        assertDefaultDeliveryRule(tenantA, organizationB);
        assertDefaultDeliveryRule(tenantB, otherTenantOrganization);
        assertZeroOrganizationPayoutAccount(tenantA, organizationA);
        assertZeroOrganizationPayoutAccount(tenantA, organizationB);
        assertZeroOrganizationPayoutAccount(
                tenantB, otherTenantOrganization);
        assertReliableFundsTaskKeySatisfiesMysqlConstraint(
                tenantA, organizationA);
        assertPayoutLiquidityAlertAndTaskWakeUseRuntimeGrants(
                tenantA, organizationA);

        String workerLogin = "v01-worker-" + run;
        JsonNode worker = data(write(
                platformManager,
                post("/api/v1/web/platform/tenants/" + tenantA
                        + "/staff-accounts"),
                UUID.randomUUID(),
                Map.of(
                        "loginName", workerLogin,
                        "initialPassword", WORKER_PASSWORD,
                        "displayName", "Worker",
                        "permissionCodes", new String[0],
                        "accountKind", "TENANT_PRINCIPAL",
                        "tenantCode", tenantB,
                        "role", "platform"),
                201));
        assertEquals("STAFF", worker.path("accountKind").asText());
        String workerUid = worker.path("staffAccountUid").asText();
        write(
                platformManager,
                post("/api/v1/web/platform/tenants/" + tenantA
                        + "/organizations/" + organizationA
                        + "/staff-memberships"),
                UUID.randomUUID(),
                Map.of(
                        "staffAccountUid", workerUid,
                        "manager", true,
                        "permissionCodes", new String[0],
                        "expectedAuthVersion", 0),
                201);

        BrowserClient workerClient = new BrowserClient();
        JsonNode workerSession = data(login(
                workerClient,
                "/api/v1/web/auth/sessions",
                workerLogin,
                WORKER_PASSWORD,
                201));
        assertEquals(1, workerSession.path("authVersion").asLong());
        read(
                workerClient,
                "/api/v1/web/organizations/" + organizationA,
                200);
        assertEquals(
                "RESOURCE.NOT_FOUND",
                json(read(
                        workerClient,
                        "/api/v1/web/organizations/" + organizationB,
                        404))
                        .path("code").asText());
        assertEquals(
                "RESOURCE.NOT_FOUND",
                json(read(
                        workerClient,
                        "/api/v1/web/organizations/"
                                + otherTenantOrganization,
                        404))
                        .path("code").asText());

        write(
                workerClient,
                post("/api/v1/web/staff-accounts/current/password-changes"),
                UUID.randomUUID(),
                Map.of(
                        "currentPassword", WORKER_PASSWORD,
                        "newPassword", "WorkerPass456!",
                        "expectedVersion",
                        workerSession.path("version").asLong(),
                        "expectedAuthVersion",
                        workerSession.path("authVersion").asLong()),
                200);
        read(
                workerClient,
                "/api/v1/web/auth/sessions/current",
                401);

        BrowserClient reauthorizedWorker = new BrowserClient();
        login(
                reauthorizedWorker,
                "/api/v1/web/auth/sessions",
                workerLogin,
                "WorkerPass456!",
                201);
        JsonNode membership = data(read(
                platformManager,
                "/api/v1/web/platform/tenants/" + tenantA
                        + "/organizations/" + organizationA
                        + "/staff-memberships/" + workerUid,
                200));
        write(
                platformManager,
                put("/api/v1/web/platform/tenants/" + tenantA
                        + "/organizations/" + organizationA
                        + "/staff-memberships/" + workerUid
                        + "/authorization"),
                UUID.randomUUID(),
                Map.of(
                        "manager", false,
                        "permissionCodes",
                        new String[]{"organization.read"},
                        "expectedVersion",
                        membership.path("version").asLong(),
                        "expectedAuthVersion",
                        membership.path("authVersion").asLong()),
                200);
        read(
                reauthorizedWorker,
                "/api/v1/web/auth/sessions/current",
                401);

        BrowserClient organizationWorker = new BrowserClient();
        login(
                organizationWorker,
                "/api/v1/web/auth/sessions",
                workerLogin,
                "WorkerPass456!",
                201);
        read(
                organizationWorker,
                "/api/v1/web/organizations/" + organizationA,
                200);
        JsonNode organizationBeforeDisable = data(read(
                platformManager,
                "/api/v1/web/platform/tenants/" + tenantA
                        + "/organizations/" + organizationA,
                200));
        write(
                platformManager,
                post("/api/v1/web/platform/tenants/" + tenantA
                        + "/organizations/" + organizationA
                        + "/deactivations"),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion",
                        organizationBeforeDisable.path("version").asLong(),
                        "reason", "integration boundary"),
                200);
        read(
                organizationWorker,
                "/api/v1/web/auth/sessions/current",
                401);

        BrowserClient principalBClient = new BrowserClient();
        login(
                principalBClient,
                "/api/v1/web/auth/sessions",
                principalLoginB,
                PRINCIPAL_PASSWORD,
                201);
        JsonNode tenantBBeforeDisable = data(read(
                platformManager,
                "/api/v1/web/platform/tenants/" + tenantB,
                200));
        write(
                platformManager,
                post("/api/v1/web/platform/tenants/" + tenantB
                        + "/deactivations"),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion",
                        tenantBBeforeDisable.path("version").asLong(),
                        "reason", "integration boundary"),
                200);
        read(
                principalBClient,
                "/api/v1/web/auth/sessions/current",
                401);

        assertTrue(jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_audit_log
                        WHERE result = 'SUCCEEDED'
                          AND entry_channel = 'WEB'
                        """, Long.class) >= 10);
        String auditJson = jdbc.queryForObject("""
                        SELECT CAST(
                            JSON_ARRAYAGG(safe_change_summary)
                            AS CHAR
                        )
                        FROM ops_audit_log
                        WHERE result = 'SUCCEEDED'
                        """, String.class);
        assertNotNull(auditJson);
        assertFalse(auditJson.contains(PLATFORM_PASSWORD));
        assertFalse(auditJson.contains(PRINCIPAL_PASSWORD));
        assertFalse(auditJson.contains(WORKER_PASSWORD));
    }

    @Test
    void concurrentSameActorIdempotentCommandsConvergeToOneTenant()
            throws Exception {
        BrowserClient initial = new BrowserClient();
        login(
                initial,
                "/api/v1/web/platform/auth/sessions",
                platformLogin,
                PLATFORM_PASSWORD,
                201);
        BrowserClient firstClient = initial.copy();
        BrowserClient secondClient = initial.copy();
        String tenantCode = code("tc");
        UUID operationUid = UUID.randomUUID();
        Map<String, String> body = Map.of(
                "tenantCode", tenantCode,
                "enterpriseName", "Concurrent tenant");
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<MvcResult> first = executor.submit(() -> {
                start.await();
                return write(
                        firstClient,
                        post("/api/v1/web/platform/tenants"),
                        operationUid,
                        body,
                        201);
            });
            Future<MvcResult> second = executor.submit(() -> {
                start.await();
                return write(
                        secondClient,
                        post("/api/v1/web/platform/tenants"),
                        operationUid,
                        body,
                        201);
            });
            start.countDown();
            first.get();
            second.get();
        } finally {
            executor.shutdownNow();
        }
        assertEquals(1, countTenant(tenantCode));
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_audit_log
                        WHERE operation_uid = ?
                          AND result = 'SUCCEEDED'
                        """, Integer.class, operationUid.toString()));
    }

    @Test
    void failedPlatformLoginsAreCommittedAndLockTheAccount()
            throws Exception {
        BrowserClient client = new BrowserClient();
        for (int attempt = 0; attempt < 5; attempt++) {
            login(
                    client,
                    "/api/v1/web/platform/auth/sessions",
                    platformLogin,
                    "WrongPassword123!",
                    401);
        }

        Map<String, Object> state = jdbc.queryForMap("""
                        SELECT failed_login_count, locked_until
                        FROM iam_platform_admin
                        WHERE login_name = ?
                        """, platformLogin);
        assertEquals(5, ((Number) state.get("failed_login_count")).intValue());
        assertNotNull(state.get("locked_until"));

        login(
                client,
                "/api/v1/web/platform/auth/sessions",
                platformLogin,
                PLATFORM_PASSWORD,
                403);
    }

    @Test
    void requiredDirectoryFieldsCannotDisappearOrClearAuthorization()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("required");
        write(
                platform,
                post("/api/v1/web/platform/tenants"),
                UUID.randomUUID(),
                Map.of(
                        "tenantCode", tenantCode,
                        "enterpriseName", "Required fields tenant"),
                201);

        write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/activations"),
                UUID.randomUUID(),
                Map.of(),
                400);

        JsonNode staff = createStaff(
                platform,
                tenantCode,
                "required-worker-" + run,
                new String[]{"tenant.read"});
        Map<String, Object> nullPermissions = new LinkedHashMap<>();
        nullPermissions.put("permissionCodes", null);
        nullPermissions.put(
                "expectedAuthVersion",
                staff.path("authVersion").asLong());
        write(
                platform,
                put("/api/v1/web/platform/tenants/" + tenantCode
                        + "/staff-accounts/"
                        + staff.path("staffAccountUid").asText()
                        + "/tenant-permissions"),
                UUID.randomUUID(),
                nullPermissions,
                400);

        String organizationCode = code("required-org");
        createOrganization(
                platform,
                tenantCode,
                organizationCode,
                "Required fields organization");
        Map<String, Object> missingManager = new LinkedHashMap<>();
        missingManager.put(
                "staffAccountUid",
                staff.path("staffAccountUid").asText());
        missingManager.put("permissionCodes", new String[0]);
        missingManager.put(
                "expectedAuthVersion",
                staff.path("authVersion").asLong());
        write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/organizations/" + organizationCode
                        + "/staff-memberships"),
                UUID.randomUUID(),
                missingManager,
                400);

        JsonNode effectiveAccess = data(read(
                platform,
                "/api/v1/web/platform/tenants/" + tenantCode
                        + "/staff-accounts/"
                        + staff.path("staffAccountUid").asText()
                        + "/effective-access",
                200));
        assertTrue(effectiveAccess.path("tenantPermissionCodes")
                .valueStream()
                .anyMatch(node -> "tenant.read".equals(node.asText())));
    }

    @Test
    void platformCanReadVersionAndPublishOrganizationDeliveryRules()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("rule-tenant");
        String organizationCode = code("rule-org");
        createTenant(platform, tenantCode, null);
        createOrganization(
                platform,
                tenantCode,
                organizationCode,
                "Delivery rule organization");
        String base = "/api/v1/web/platform/tenants/" + tenantCode
                + "/organizations/" + organizationCode;

        JsonNode initial = data(read(
                platform,
                base + "/delivery-configuration",
                200));
        assertEquals(1, initial.path("versionNo").asLong());
        assertEquals(
                "ALL_MANUAL",
                initial.path("reviewMode").asText());
        assertEquals(
                "-10.00",
                initial.path("openBalanceFloorYuan").asText());
        assertEquals(
                "100.000",
                initial.path("maxReviewAbsoluteWeightKg").asText());
        assertTrue(initial.path("current").asBoolean());

        JsonNode firstPage = data(read(
                platform,
                base + "/delivery-configuration-versions?limit=20",
                200));
        assertEquals(1, firstPage.path("items").size());
        assertTrue(firstPage.path("nextBeforeVersionNo").isNull());

        UUID operationUid = UUID.randomUUID();
        Map<String, Object> release = Map.of(
                "expectedLatestVersion", 1,
                "reviewMode", "ALL_MANUAL",
                "openBalanceFloorYuan", "-20.00",
                "maxReviewAbsoluteWeightKg", "150.000",
                "reason", "integration delivery rule");
        JsonNode published = data(write(
                platform,
                post(base + "/delivery-configuration-releases"),
                operationUid,
                release,
                201));
        assertEquals(2, published.path("versionNo").asLong());
        assertEquals(
                "-20.00",
                published.path("openBalanceFloorYuan").asText());
        assertEquals(
                "150.000",
                published.path("maxReviewAbsoluteWeightKg").asText());

        JsonNode replayed = data(write(
                platform,
                post(base + "/delivery-configuration-releases"),
                operationUid,
                release,
                201));
        assertEquals(
                published.path("contentSha256").asText(),
                replayed.path("contentSha256").asText());
        assertEquals(
                published.path("publishedAt").asText(),
                replayed.path("publishedAt").asText());

        MvcResult stale = write(
                platform,
                post(base + "/delivery-configuration-releases"),
                UUID.randomUUID(),
                Map.of(
                        "expectedLatestVersion", 1,
                        "reviewMode", "ALL_MANUAL",
                        "openBalanceFloorYuan", "-30.00",
                        "maxReviewAbsoluteWeightKg", "200.000"),
                409);
        assertEquals(
                "DELIVERY.CONFIGURATION_VERSION_CONFLICT",
                json(stale).path("code").asText());
        assertEquals(
                2,
                json(stale).path("details")
                        .path("currentVersion").asLong());

        MvcResult automaticReview = write(
                platform,
                post(base + "/delivery-configuration-releases"),
                UUID.randomUUID(),
                Map.of(
                        "expectedLatestVersion", 2,
                        "reviewMode", "AUTO_AFTER_24H",
                        "openBalanceFloorYuan", "-30.00",
                        "maxReviewAbsoluteWeightKg", "200.000"),
                422);
        assertEquals(
                "DELIVERY.REVIEW_MODE_NOT_AVAILABLE",
                json(automaticReview).path("code").asText());

        JsonNode versionOne = data(read(
                platform,
                base + "/delivery-configuration-versions/1",
                200));
        assertFalse(versionOne.path("current").asBoolean());
        JsonNode current = data(read(
                platform,
                base + "/delivery-configuration",
                200));
        assertEquals(2, current.path("versionNo").asLong());

        assertEquals(
                "2|-2000|150000|1",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    head.current_version_no, '|',
                                    config.open_balance_floor_cent, '|',
                                    config.max_review_abs_weight_g, '|',
                                    head.lock_version
                                )
                                FROM iam_tenant tenant
                                JOIN iam_organization organization
                                  ON organization.tenant_id = tenant.id
                                JOIN
                                    rec_organization_delivery_config_head head
                                  ON head.tenant_id = tenant.id
                                 AND head.organization_id = organization.id
                                JOIN rec_organization_delivery_config config
                                  ON config.id = head.current_config_id
                                 AND config.tenant_id = head.tenant_id
                                 AND config.organization_id =
                                     head.organization_id
                                WHERE tenant.tenant_code = ?
                                  AND organization.organization_code = ?
                                """,
                        String.class,
                        tenantCode,
                        organizationCode));
        assertEquals(
                1,
                jdbc.queryForObject("""
                                SELECT COUNT(*)
                                FROM ops_audit_log
                                WHERE action_code =
                                    'delivery.configuration.release'
                                  AND operation_uid = ?
                                """,
                        Integer.class,
                        operationUid.toString()));
    }

    @Test
    void idempotencyFingerprintIncludesTheTargetResource()
            throws Exception {
        BrowserClient platform = platformClient();
        String firstTenant = code("idem-a");
        String secondTenant = code("idem-b");
        createTenant(platform, firstTenant, null);
        createTenant(platform, secondTenant, null);
        String organizationCode = code("same-org");
        UUID operationUid = UUID.randomUUID();
        Map<String, Object> body = Map.of(
                "organizationCode", organizationCode,
                "organizationName", "Same request body");

        write(
                platform,
                post("/api/v1/web/platform/tenants/" + firstTenant
                        + "/organizations"),
                operationUid,
                body,
                201);
        MvcResult conflict = write(
                platform,
                post("/api/v1/web/platform/tenants/" + secondTenant
                        + "/organizations"),
                operationUid,
                body,
                409);

        assertEquals(
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                json(conflict).path("code").asText());
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM iam_organization o
                        JOIN iam_tenant t ON t.id = o.tenant_id
                        WHERE t.tenant_code = ?
                          AND o.organization_code = ?
                        """,
                Integer.class,
                secondTenant,
                organizationCode));
    }

    @Test
    void unknownPlatformLoginUsingDevelopmentPasswordIsRejected()
            throws Exception {
        MvcResult result = login(
                new BrowserClient(),
                "/api/v1/web/platform/auth/sessions",
                "missing-platform-" + run,
                "admin123",
                401);

        assertEquals(
                "AUTH.INVALID_CREDENTIALS",
                json(result).path("code").asText());
    }

    @Test
    void unknownStaffLoginUsingDevelopmentPasswordIsRejected()
            throws Exception {
        MvcResult result = login(
                new BrowserClient(),
                "/api/v1/web/auth/sessions",
                "missing-staff-" + run,
                "admin123",
                401);

        assertEquals(
                "AUTH.INVALID_CREDENTIALS",
                json(result).path("code").asText());
    }

    @Test
    void auditIsRedactedAndCoversPrivilegedReadsAndLoginDenials()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("audit");
        String forbiddenPhone = "13977778888";
        createTenant(platform, tenantCode, forbiddenPhone);

        read(
                platform,
                "/api/v1/web/platform/tenants/" + tenantCode,
                200);
        login(
                new BrowserClient(),
                "/api/v1/web/platform/auth/sessions",
                "missing-" + run,
                "WrongPassword123!",
                401);

        String summaries = jdbc.queryForObject("""
                        SELECT COALESCE(
                            CAST(JSON_ARRAYAGG(safe_change_summary) AS CHAR),
                            '[]'
                        )
                        FROM ops_audit_log
                        WHERE target_stable_key = ?
                        """, String.class, tenantCode);
        assertFalse(summaries.contains(forbiddenPhone));
        assertTrue(jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_audit_log
                        WHERE action_code =
                              'identity.platform.privileged-read'
                          AND result = 'SUCCEEDED'
                          AND target_stable_key = ?
                        """, Integer.class, tenantCode) >= 1);
        assertTrue(jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_audit_log
                        WHERE action_code = 'identity.auth.platform.login'
                          AND actor_kind = 'UNAUTHENTICATED'
                          AND result = 'DENIED'
                        """, Integer.class) >= 1);
    }

    @Test
    void staffCannotCreateTheirOwnManagerMembership()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("self");
        createEnabledTenant(platform, tenantCode);
        String organizationCode = code("self-org");
        createAndActivateOrganization(
                platform,
                tenantCode,
                organizationCode,
                "Self authorization organization");
        String attackerLogin = "self-worker-" + run;
        JsonNode attacker = createStaff(
                platform,
                tenantCode,
                attackerLogin,
                new String[]{"staff.manage", "organization-manager.manage"});

        BrowserClient attackerClient = new BrowserClient();
        login(
                attackerClient,
                "/api/v1/web/auth/sessions",
                attackerLogin,
                WORKER_PASSWORD,
                201);
        MvcResult denied = write(
                attackerClient,
                post("/api/v1/web/organizations/" + organizationCode
                        + "/staff-memberships"),
                UUID.randomUUID(),
                Map.of(
                        "staffAccountUid",
                        attacker.path("staffAccountUid").asText(),
                        "manager", true,
                        "permissionCodes", new String[0],
                        "expectedAuthVersion",
                        attacker.path("authVersion").asLong()),
                409);

        assertEquals(
                "IDENTITY.NATURAL_AUTHORITY_IMMUTABLE",
                json(denied).path("code").asText());
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM iam_organization_staff_membership m
                        JOIN iam_staff_account s
                          ON s.tenant_id = m.tenant_id
                         AND s.id = m.staff_account_id
                        WHERE s.staff_account_uid = ?
                        """,
                Integer.class,
                attacker.path("staffAccountUid").asText()));
        assertTrue(jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_audit_log
                        WHERE result = 'DENIED'
                          AND actor_kind = 'STAFF_ACCOUNT'
                          AND action_code = 'identity.membership.create'
                          AND target_stable_key = ?
                        """,
                Integer.class,
                "tenant:" + tenantCode
                        + "|organization:" + organizationCode
                        + "|staff:"
                        + attacker.path("staffAccountUid").asText()) >= 1);
    }

    @Test
    void organizationPermissionReaderSeesOnlyTheSharedOrganization()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("access");
        createEnabledTenant(platform, tenantCode);
        String organizationA = code("access-a");
        String organizationB = code("access-b");
        createAndActivateOrganization(
                platform, tenantCode, organizationA, "Access A");
        createAndActivateOrganization(
                platform, tenantCode, organizationB, "Access B");

        JsonNode target = createStaff(
                platform,
                tenantCode,
                "access-target-" + run,
                new String[]{"tenant.read"});
        long authVersion = target.path("authVersion").asLong();
        createMembership(
                platform,
                tenantCode,
                organizationA,
                target.path("staffAccountUid").asText(),
                false,
                new String[]{"organization.read"},
                authVersion);
        createMembership(
                platform,
                tenantCode,
                organizationB,
                target.path("staffAccountUid").asText(),
                false,
                new String[]{"staff.read"},
                authVersion + 1);

        String readerLogin = "access-reader-" + run;
        write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/organizations/" + organizationA
                        + "/staff-account-provisionings"),
                UUID.randomUUID(),
                Map.of(
                        "loginName", readerLogin,
                        "initialPassword", WORKER_PASSWORD,
                        "displayName", "Organization reader",
                        "manager", true,
                        "permissionCodes", new String[0]),
                201);
        BrowserClient reader = new BrowserClient();
        login(
                reader,
                "/api/v1/web/auth/sessions",
                readerLogin,
                WORKER_PASSWORD,
                201);

        JsonNode access = data(read(
                reader,
                "/api/v1/web/staff-accounts/"
                        + target.path("staffAccountUid").asText()
                        + "/effective-access",
                200));
        assertEquals(0, access.path("tenantPermissionCodes").size());
        assertEquals(1, access.path("organizations").size());
        assertEquals(
                organizationA,
                access.path("organizations").get(0)
                        .path("organizationCode").asText());
    }

    @Test
    void organizationMiniappConfigurationControlsAppIdAndLiveLoginSessions()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("miniapp-t");
        String organizationCode = code("miniapp-o");
        createEnabledTenant(platform, tenantCode);
        createAndActivateOrganization(
                platform,
                tenantCode,
                organizationCode,
                "Miniapp organization");
        String base = "/api/v1/web/platform/tenants/" + tenantCode
                + "/organizations/" + organizationCode;
        String appId = "wx" + UUID.randomUUID().toString()
                .replace("-", "").substring(0, 16);
        String initialSecret = "fake-initial-app-secret-" + run;
        UUID initialConfigurationUid = UUID.randomUUID();
        Map<String, Object> initialConfiguration = Map.of(
                "appId", appId,
                "displayName", "Miniapp A",
                "appSecret", initialSecret);
        MvcResult createdResult = write(
                platform,
                put(base + "/miniapp-configuration"),
                initialConfigurationUid,
                initialConfiguration,
                200);
        JsonNode created = data(createdResult);
        assertEquals(appId, created.path("appId").asText());
        assertTrue(created.path("appSecretConfigured").asBoolean());
        assertFalse(created.path("activated").asBoolean());
        assertFalse(created.path("loginEnabled").asBoolean());
        assertEquals(0, created.path("version").asLong());
        assertFalse(createdResult.getResponse()
                .getContentAsString().contains(initialSecret));
        assertEquals(
                0,
                data(write(
                        platform,
                        put(base + "/miniapp-configuration"),
                        initialConfigurationUid,
                        initialConfiguration,
                        200)).path("version").asLong());
        MvcResult secretIdempotencyConflict = write(
                platform,
                put(base + "/miniapp-configuration"),
                initialConfigurationUid,
                Map.of(
                        "appId", appId,
                        "displayName", "Miniapp A",
                        "appSecret", initialSecret + "-different"),
                409);
        assertEquals(
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                json(secretIdempotencyConflict).path("code").asText());

        MvcResult readResult = read(
                platform,
                base + "/miniapp-configuration",
                200);
        assertEquals(
                "no-store",
                readResult.getResponse().getHeader("Cache-Control"));
        assertEquals(
                initialSecret,
                data(readResult).path("appSecret").asText());

        MvcResult prematureEnable = write(
                platform,
                post(base + "/miniapp-login/enablements"),
                UUID.randomUUID(),
                Map.of("expectedVersion", 0),
                422);
        assertEquals(
                "IDENTITY.MINIAPP_CONFIGURATION_INVALID",
                json(prematureEnable).path("code").asText());

        JsonNode activated = data(write(
                platform,
                post(base + "/miniapp-configuration/activations"),
                UUID.randomUUID(),
                Map.of("expectedVersion", 0),
                200));
        assertTrue(activated.path("activated").asBoolean());
        assertEquals(1, activated.path("version").asLong());

        MvcResult immutableAppId = write(
                platform,
                put(base + "/miniapp-configuration"),
                UUID.randomUUID(),
                Map.of(
                        "appId", "wx" + UUID.randomUUID().toString()
                                .replace("-", "").substring(0, 16),
                        "displayName", "Changed",
                        "expectedVersion", 1),
                409);
        assertEquals(
                "IDENTITY.MINIAPP_ALREADY_ACTIVATED",
                json(immutableAppId).path("code").asText());

        String rotatedSecret = "fake-rotated-app-secret-" + run;
        JsonNode rotated = data(write(
                platform,
                put(base + "/miniapp-configuration"),
                UUID.randomUUID(),
                Map.of(
                        "appId", appId,
                        "displayName", "Miniapp A rotated",
                        "appSecret", rotatedSecret,
                        "expectedVersion", 1),
                200));
        assertEquals(2, rotated.path("version").asLong());
        assertEquals(
                rotatedSecret,
                data(read(
                        platform,
                        base + "/miniapp-configuration",
                        200)).path("appSecret").asText());
        BrowserClient tenantPrincipal = new BrowserClient();
        login(
                tenantPrincipal,
                "/api/v1/web/auth/sessions",
                "principal-" + tenantCode,
                PRINCIPAL_PASSWORD,
                201);
        assertEquals(
                rotatedSecret,
                data(read(
                        tenantPrincipal,
                        "/api/v1/web/organizations/"
                                + organizationCode
                                + "/miniapp-configuration",
                        200)).path("appSecret").asText());

        JsonNode enabled = data(write(
                platform,
                post(base + "/miniapp-login/enablements"),
                UUID.randomUUID(),
                Map.of("expectedVersion", 2),
                200));
        assertTrue(enabled.path("loginEnabled").asBoolean());
        assertEquals(3, enabled.path("version").asLong());

        MvcResult miniappLogin = mockMvc.perform(
                        post("/api/v1/miniapp/auth/sessions")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(objectMapper.writeValueAsBytes(
                                        Map.of(
                                                "appId", appId,
                                                "wxLoginCode",
                                                "fake:miniapp-" + run))))
                .andReturn();
        assertEquals(
                201,
                miniappLogin.getResponse().getStatus(),
                miniappLogin.getResponse().getContentAsString());
        String bearer = data(miniappLogin)
                .path("accessToken").asText();
        assertFalse(bearer.isBlank());

        JsonNode disabled = data(write(
                platform,
                post(base + "/miniapp-login/disablements"),
                UUID.randomUUID(),
                Map.of("expectedVersion", 3),
                200));
        assertFalse(disabled.path("loginEnabled").asBoolean());
        assertEquals(4, disabled.path("version").asLong());

        MvcResult revokedSession = mockMvc.perform(
                        get("/api/v1/miniapp/auth/sessions/current")
                                .header("Authorization",
                                        "Bearer " + bearer))
                .andReturn();
        assertEquals(
                401,
                revokedSession.getResponse().getStatus(),
                revokedSession.getResponse().getContentAsString());
    }

    private BrowserClient platformClient() throws Exception {
        BrowserClient platform = new BrowserClient();
        login(
                platform,
                "/api/v1/web/platform/auth/sessions",
                platformLogin,
                PLATFORM_PASSWORD,
                201);
        return platform;
    }

    private JsonNode createTenant(
            BrowserClient platform,
            String tenantCode,
            String contactPhone) throws Exception {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("tenantCode", tenantCode);
        body.put("enterpriseName", "Tenant " + tenantCode);
        if (contactPhone != null) {
            body.put("contactPhone", contactPhone);
        }
        return data(write(
                platform,
                post("/api/v1/web/platform/tenants"),
                UUID.randomUUID(),
                body,
                201));
    }

    private void createEnabledTenant(
            BrowserClient platform,
            String tenantCode) throws Exception {
        createTenant(platform, tenantCode, null);
        write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/principal-account"),
                UUID.randomUUID(),
                Map.of(
                        "loginName", "principal-" + tenantCode,
                        "initialPassword", PRINCIPAL_PASSWORD,
                        "displayName", "Principal " + tenantCode,
                        "expectedVersion", 0),
                201);
        activateTenant(platform, tenantCode);
    }

    private JsonNode createOrganization(
            BrowserClient platform,
            String tenantCode,
            String organizationCode,
            String name) throws Exception {
        return data(write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/organizations"),
                UUID.randomUUID(),
                Map.of(
                        "organizationCode", organizationCode,
                        "organizationName", name),
                201));
    }

    private JsonNode createStaff(
            BrowserClient platform,
            String tenantCode,
            String loginName,
            String[] permissions) throws Exception {
        return data(write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/staff-accounts"),
                UUID.randomUUID(),
                Map.of(
                        "loginName", loginName,
                        "initialPassword", WORKER_PASSWORD,
                        "displayName", loginName,
                        "permissionCodes", permissions),
                201));
    }

    private JsonNode createMembership(
            BrowserClient platform,
            String tenantCode,
            String organizationCode,
            String staffUid,
            boolean manager,
            String[] permissions,
            long expectedAuthVersion) throws Exception {
        return data(write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/organizations/" + organizationCode
                        + "/staff-memberships"),
                UUID.randomUUID(),
                Map.of(
                        "staffAccountUid", staffUid,
                        "manager", manager,
                        "permissionCodes", permissions,
                        "expectedAuthVersion", expectedAuthVersion),
                201));
    }

    private void activateTenant(BrowserClient client, String tenantCode)
            throws Exception {
        long version = data(read(
                client,
                "/api/v1/web/platform/tenants/" + tenantCode,
                200)).path("version").asLong();
        write(
                client,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/activations"),
                UUID.randomUUID(),
                Map.of("expectedVersion", version),
                200);
    }

    private void createAndActivateOrganization(
            BrowserClient client,
            String tenantCode,
            String organizationCode,
            String name) throws Exception {
        JsonNode organization = createOrganization(
                client, tenantCode, organizationCode, name);
        write(
                client,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/organizations/" + organizationCode
                        + "/activations"),
                UUID.randomUUID(),
                Map.of("expectedVersion",
                        organization.path("version").asLong()),
                200);
    }

    private void assertDefaultDeliveryRule(
            String tenantCode,
            String organizationCode) {
        assertEquals(
                "1|ALL_MANUAL|-1000|100000|SYSTEM|32",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    config.version_no, '|',
                                    config.review_mode, '|',
                                    config.open_balance_floor_cent, '|',
                                    config.max_review_abs_weight_g, '|',
                                    config.publication_source, '|',
                                    OCTET_LENGTH(config.content_sha256)
                                )
                                FROM iam_tenant tenant
                                JOIN iam_organization organization
                                  ON organization.tenant_id = tenant.id
                                JOIN
                                    rec_organization_delivery_config_head head
                                  ON head.tenant_id = tenant.id
                                 AND head.organization_id = organization.id
                                JOIN rec_organization_delivery_config config
                                  ON config.id = head.current_config_id
                                 AND config.tenant_id = head.tenant_id
                                 AND config.organization_id =
                                     head.organization_id
                                WHERE tenant.tenant_code = ?
                                  AND organization.organization_code = ?
                                """,
                        String.class,
                        tenantCode,
                        organizationCode));
        assertEquals(
                1,
                jdbc.queryForObject("""
                                SELECT COUNT(*)
                                FROM iam_tenant tenant
                                JOIN iam_organization organization
                                  ON organization.tenant_id = tenant.id
                                JOIN rec_organization_order_counter counter
                                  ON counter.tenant_id = tenant.id
                                 AND counter.organization_id =
                                     organization.id
                                WHERE tenant.tenant_code = ?
                                  AND organization.organization_code = ?
                                  AND counter.last_visibility_sequence_no = 0
                                """,
                        Integer.class,
                        tenantCode,
                        organizationCode));
    }

    private void assertZeroOrganizationPayoutAccount(
            String tenantCode,
            String organizationCode) {
        assertEquals(
                "1|0|0|0",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    COUNT(*), '|',
                                    COALESCE(MAX(
                                        account.available_payout_cent), -1), '|',
                                    COALESCE(MAX(
                                        account.frozen_withdrawal_cent), -1), '|',
                                    COALESCE(MAX(account.lock_version), -1)
                                )
                                FROM iam_tenant tenant
                                JOIN iam_organization organization
                                  ON organization.tenant_id = tenant.id
                                LEFT JOIN fund_organization_payout_account account
                                  ON account.tenant_id = organization.tenant_id
                                 AND account.organization_id = organization.id
                                WHERE tenant.tenant_code = ?
                                  AND organization.organization_code = ?
                                """,
                        String.class,
                        tenantCode,
                        organizationCode));
        assertEquals(
                "1|1000|10|1000|0",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    COUNT(*), '|',
                                    MAX(config.hard_limit_cent), '|',
                                    MAX(config.manual_min_cent), '|',
                                    MAX(config.manual_max_cent), '|',
                                    MAX(config.manual_review_free_threshold_cent)
                                )
                                FROM iam_tenant tenant
                                JOIN iam_organization organization
                                  ON organization.tenant_id = tenant.id
                                JOIN fund_organization_withdraw_config_head head
                                  ON head.tenant_id = organization.tenant_id
                                 AND head.organization_id = organization.id
                                JOIN fund_organization_withdraw_config config
                                  ON config.id = head.current_config_id
                                WHERE tenant.tenant_code = ?
                                  AND organization.organization_code = ?
                                """, String.class,
                        tenantCode, organizationCode));
    }

    private void assertReliableFundsTaskKeySatisfiesMysqlConstraint(
            String tenantCode,
            String organizationCode) {
        String rechargeNo = RechargeApplicationService.stableNo(
                "RC",
                UUID.fromString("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"));
        String storedTaskKey = new TransactionTemplate(transactionManager)
                .execute(status -> {
                    long[] scope = jdbc.queryForObject("""
                                    SELECT tenant.id, organization.id
                                    FROM iam_tenant tenant
                                    JOIN iam_organization organization
                                      ON organization.tenant_id = tenant.id
                                    WHERE tenant.tenant_code = ?
                                      AND organization.organization_code = ?
                                    """,
                            (rs, ignored) -> new long[]{
                                    rs.getLong(1), rs.getLong(2)},
                            tenantCode, organizationCode);
                    reliableFundsTasks.register(
                            new ReliableFundsTaskRegistrationPort
                                    .ReliableFundsTaskRegistration(
                                    scope[0], scope[1],
                                    "CREATE_NATIVE_PAYMENT",
                                    "CREATE_NATIVE_PAYMENT:" + rechargeNo,
                                    "RECHARGE_ORDER", rechargeNo, 1, "{}",
                                    RechargeApplicationService.sha256("{}"),
                                    20, null));
                    String taskKey = jdbc.queryForObject("""
                                    SELECT task_key FROM ops_reliable_task
                                    WHERE target_type = 'RECHARGE_ORDER'
                                      AND target_stable_key = ?
                                    """, String.class, rechargeNo);
                    status.setRollbackOnly();
                    return taskKey;
                });
        assertEquals(
                ("CREATE_NATIVE_PAYMENT:" + rechargeNo)
                        .toUpperCase(Locale.ROOT),
                storedTaskKey);
    }

    private void assertPayoutLiquidityAlertAndTaskWakeUseRuntimeGrants(
            String tenantCode,
            String organizationCode) {
        new TransactionTemplate(transactionManager).executeWithoutResult(status -> {
            long[] scope = jdbc.queryForObject("""
                            SELECT tenant.id, organization.id
                            FROM iam_tenant tenant
                            JOIN iam_organization organization
                              ON organization.tenant_id = tenant.id
                            WHERE tenant.tenant_code = ?
                              AND organization.organization_code = ?
                            """,
                    (rs, ignored) -> new long[]{rs.getLong(1), rs.getLong(2)},
                    tenantCode, organizationCode);
            long merchantId = jdbc.queryForObject("""
                    SELECT id FROM fund_wechat_merchant_profile
                    WHERE status = 'ENABLED' ORDER BY id LIMIT 1
                    """, Long.class);
            String withdrawalNo = "WDaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
            reliableFundsTasks.register(
                    new ReliableFundsTaskRegistrationPort
                            .ReliableFundsTaskRegistration(
                            scope[0], scope[1],
                            "SUBMIT_MERCHANT_TRANSFER",
                            "SUBMIT_MERCHANT_TRANSFER:" + withdrawalNo,
                            "WITHDRAWAL_ORDER", withdrawalNo, 1, "{}",
                            RechargeApplicationService.sha256("{}"),
                            20, null));
            UUID pausedEventUid = UUID.randomUUID();
            LocalDateTime now = jdbc.queryForObject(
                    "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
            fundsOperationalControl.observePayoutLiquidityPause(
                    merchantId, pausedEventUid, now);
            assertEquals("OPEN|1", jdbc.queryForObject("""
                            SELECT CONCAT(status, '|', discovery_count)
                            FROM ops_alert
                            WHERE source_type = 'PAYOUT_GATE_PAUSE'
                              AND source_key = ?
                            """, String.class,
                    "PAYOUT_GATE:" + merchantId + ":" + pausedEventUid));
            fundsOperationalControl.wakePayoutTasks(now);
            assertEquals(1L, jdbc.queryForObject("""
                            SELECT wake_version FROM ops_reliable_task
                            WHERE task_key = ?
                            """, Long.class,
                    ("SUBMIT_MERCHANT_TRANSFER:" + withdrawalNo)
                            .toUpperCase(Locale.ROOT)));
            fundsOperationalControl.resolvePayoutLiquidityPause(
                    merchantId, pausedEventUid, now);
            assertEquals("RESOLVED", jdbc.queryForObject("""
                            SELECT status FROM ops_alert
                            WHERE source_type = 'PAYOUT_GATE_PAUSE'
                              AND source_key = ?
                            """, String.class,
                    "PAYOUT_GATE:" + merchantId + ":" + pausedEventUid));
            status.setRollbackOnly();
        });
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
                Map.of(
                        "loginName", loginName,
                        "password", password),
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
        return cookies.length == 0 ? request : request.cookie(cookies);
    }

    private JsonNode data(MvcResult result) throws Exception {
        return json(result).path("data");
    }

    private JsonNode json(MvcResult result) throws Exception {
        return objectMapper.readTree(
                result.getResponse().getContentAsByteArray());
    }

    private int countTenant(String tenantCode) {
        return jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM iam_tenant
                        WHERE tenant_code = ?
                        """, Integer.class, tenantCode);
    }

    private String code(String prefix) {
        return prefix + "-" + run;
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

        private BrowserClient copy() {
            BrowserClient copy = new BrowserClient();
            cookies.forEach((name, cookie) ->
                    copy.cookies.put(name, (Cookie) cookie.clone()));
            return copy;
        }
    }
}
