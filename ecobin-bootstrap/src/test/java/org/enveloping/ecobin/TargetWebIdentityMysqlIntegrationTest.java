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
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
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
        "onenet.subscription.enabled=false"
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
        JsonNode organization = data(write(
                client,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/organizations"),
                UUID.randomUUID(),
                Map.of(
                        "organizationCode", organizationCode,
                        "organizationName", name),
                201));
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
