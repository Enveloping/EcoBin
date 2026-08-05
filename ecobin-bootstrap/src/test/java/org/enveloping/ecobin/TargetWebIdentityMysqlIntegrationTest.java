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
import org.enveloping.ecobin.funds.api.port.MerchantTransferChannelPort;
import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsAttemptBoundaryPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort;
import org.enveloping.ecobin.funds.application.access.FundsAccessService;
import org.enveloping.ecobin.funds.application.recharge.RechargeApplicationService;
import org.enveloping.ecobin.funds.application.pagination.FundsListCursorCodec;
import org.enveloping.ecobin.funds.application.withdrawal.WithdrawalApplicationService;
import org.enveloping.ecobin.funds.web.v1.FundsModels.CreateWithdrawalRequest;
import org.enveloping.ecobin.funds.web.v1.FundsModels.PayoutGateView;
import org.enveloping.ecobin.funds.web.v1.FundsModels.RestorePayoutGateRequest;
import org.enveloping.ecobin.funds.web.v1.FundsModels.VersionedWithdrawalRequest;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.FundsIdentityAccessPort;
import org.enveloping.ecobin.identity.api.port.FundsIdentityAccessPort.AuthorizedPlatformIdentity;
import org.enveloping.ecobin.identity.api.port.FundsIdentityAccessPort.AuthorizedWebIdentity;
import org.enveloping.ecobin.identity.api.port.FundsIdentityAccessPort.CurrentMiniappIdentity;
import org.enveloping.ecobin.identity.application.security.FundsIdentityAccessService;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
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

    @Autowired
    private FundsListCursorCodec fundsListCursorCodec;

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
    void withdrawalCreationUsesConfigBeforeBindingUnderMysqlConcurrency()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("tl");
        String organizationCode = code("ol");
        createEnabledTenant(platform, tenantCode);
        createAndActivateOrganization(
                platform, tenantCode, organizationCode,
                "Withdrawal lock order");
        WithdrawalCreationFixture fixture = seedWithdrawalCreationFixture(
                tenantCode, organizationCode);

        FundsIdentityAccessPort identity = mock(FundsIdentityAccessPort.class);
        CurrentMiniappIdentity actor = new CurrentMiniappIdentity(
                fixture.tenantId(), tenantCode, fixture.organizationId(),
                organizationCode, fixture.miniappId(), fixture.appid(),
                fixture.userId(), fixture.userUid(), UUID.randomUUID(),
                "lock-order-user");
        when(identity.currentMiniapp(true)).thenReturn(actor);
        when(identity.currentMiniapp(false)).thenReturn(actor);
        when(identity.lockWithdrawalTransferIdentity(any()))
                .thenReturn(true);
        AuditPort audit = mock(AuditPort.class);
        when(audit.append(any())).thenReturn(1L);
        WithdrawalApplicationService service =
                new WithdrawalApplicationService(
                        jdbc, new FundsAccessService(jdbc, identity),
                        reliableFundsTasks,
                        mock(ReliableFundsAttemptBoundaryPort.class),
                        mock(MerchantTransferChannelPort.class),
                        new TransactionTemplate(transactionManager), audit,
                        fundsOperationalControl, fundsListCursorCodec,
                        "https://fake.invalid");

        CountDownLatch configLocked = new CountDownLatch(1);
        CountDownLatch tryBinding = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<Void> competingConfiguration = executor.submit(() -> {
                new TransactionTemplate(transactionManager).executeWithoutResult(
                        status -> {
                            jdbc.queryForObject("""
                                    SELECT h.current_config_id
                                    FROM fund_organization_withdraw_config_head h
                                    WHERE h.tenant_id = ?
                                      AND h.organization_id = ?
                                    FOR UPDATE
                                    """, Long.class, fixture.tenantId(),
                                    fixture.organizationId());
                            configLocked.countDown();
                            try {
                                if (!tryBinding.await(5, TimeUnit.SECONDS)) {
                                    throw new IllegalStateException(
                                            "binding lock was not released");
                                }
                            } catch (InterruptedException interrupted) {
                                Thread.currentThread().interrupt();
                                throw new IllegalStateException(interrupted);
                            }
                            jdbc.queryForObject("""
                                    SELECT id
                                    FROM fund_miniapp_merchant_binding
                                    WHERE id = ? FOR UPDATE
                                    """, Long.class, fixture.bindingId());
                        });
                return null;
            });
            if (!configLocked.await(5, TimeUnit.SECONDS)) {
                competingConfiguration.get(1, TimeUnit.SECONDS);
                throw new AssertionError("config lock was not acquired");
            }
            UUID operationUid = UUID.randomUUID();
            Future<?> creation = executor.submit(() ->
                    new TransactionTemplate(transactionManager).execute(
                            status -> service.create(
                                    operationUid,
                                    new CreateWithdrawalRequest("0.10"))));

            Thread.sleep(500);
            if (creation.isDone()) {
                creation.get(1, TimeUnit.SECONDS);
            }
            assertFalse(creation.isDone(),
                    "creation should be waiting on the locked config row");
            tryBinding.countDown();
            competingConfiguration.get(10, TimeUnit.SECONDS);
            creation.get(10, TimeUnit.SECONDS);

            String withdrawalNo = RechargeApplicationService.stableNo(
                    "WD", operationUid);
            assertEquals("PENDING_REVIEW|10", jdbc.queryForObject("""
                    SELECT CONCAT(business_state, '|', amount_cent)
                    FROM fund_withdrawal_order
                    WHERE withdrawal_order_no = ?
                    """, String.class, withdrawalNo));
            assertEquals(1, jdbc.queryForObject("""
                    SELECT COUNT(*) FROM fund_active_withdrawal active
                    JOIN fund_withdrawal_order withdrawal
                      ON withdrawal.id = active.withdrawal_order_id
                    WHERE withdrawal.withdrawal_order_no = ?
                    """, Integer.class, withdrawalNo));
        } finally {
            tryBinding.countDown();
            executor.shutdownNow();
        }
    }

    @Test
    void merchantTransferNotFoundResubmitsOriginalAndTrustedQuerySettles()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("tr");
        String organizationCode = code("or");
        createEnabledTenant(platform, tenantCode);
        createAndActivateOrganization(
                platform, tenantCode, organizationCode,
                "Transfer retry state machine");
        WithdrawalCreationFixture fixture = seedWithdrawalCreationFixture(
                tenantCode, organizationCode);

        FundsIdentityAccessPort identity = mock(FundsIdentityAccessPort.class);
        CurrentMiniappIdentity actor = new CurrentMiniappIdentity(
                fixture.tenantId(), tenantCode, fixture.organizationId(),
                organizationCode, fixture.miniappId(), fixture.appid(),
                fixture.userId(), fixture.userUid(), UUID.randomUUID(),
                "transfer-retry-user");
        when(identity.currentMiniapp(true)).thenReturn(actor);
        when(identity.currentMiniapp(false)).thenReturn(actor);
        when(identity.lockWithdrawalTransferIdentity(any()))
                .thenReturn(true);
        AuditPort audit = mock(AuditPort.class);
        when(audit.append(any())).thenReturn(1L);
        ScriptedMerchantTransferChannel channel =
                new ScriptedMerchantTransferChannel();
        WithdrawalApplicationService service =
                new WithdrawalApplicationService(
                        jdbc, new FundsAccessService(jdbc, identity),
                        reliableFundsTasks,
                        mock(ReliableFundsAttemptBoundaryPort.class), channel,
                        new TransactionTemplate(transactionManager), audit,
                        fundsOperationalControl, fundsListCursorCodec,
                        "https://fake.invalid");

        UUID createOperationUid = UUID.randomUUID();
        service.create(createOperationUid, new CreateWithdrawalRequest("0.10"));
        String withdrawalNo = RechargeApplicationService.stableNo(
                "WD", createOperationUid);
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        assertEquals(1, jdbc.update("""
                UPDATE fund_withdrawal_order
                SET business_state = 'READY_TO_SUBMIT', reviewed_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE withdrawal_order_no = ?
                  AND business_state = 'PENDING_REVIEW'
                """, now, now, withdrawalNo));
        String snapshot = "{\"withdrawalNo\":\"" + withdrawalNo + "\"}";
        UUID taskUid = new TransactionTemplate(transactionManager).execute(
                status -> reliableFundsTasks.register(
                        new ReliableFundsTaskRegistrationPort
                                .ReliableFundsTaskRegistration(
                                fixture.tenantId(), fixture.organizationId(),
                                "SUBMIT_MERCHANT_TRANSFER",
                                "SUBMIT_MERCHANT_TRANSFER:" + withdrawalNo,
                                "WITHDRAWAL_ORDER", withdrawalNo, 1, snapshot,
                                RechargeApplicationService.sha256(snapshot),
                                500, null)));
        assertNotNull(taskUid);
        long taskId = jdbc.queryForObject("""
                SELECT id FROM ops_reliable_task WHERE task_uid = ?
                """, Long.class, taskUid.toString());
        assertEquals(500, jdbc.queryForObject("""
                SELECT max_auto_attempts FROM ops_reliable_task
                WHERE id = ?
                """, Integer.class, taskId));

        ReliableFundsTaskExecutorPort.Result first = service.executeTask(
                fundsCommand(taskUid, taskId, 1, fixture, withdrawalNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.RETRY,
                first.outcome());
        ReliableFundsTaskExecutorPort.Result second = service.executeTask(
                fundsCommand(taskUid, taskId, 2, fixture, withdrawalNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.WAITING,
                second.outcome());
        ReliableFundsTaskExecutorPort.Result third = service.executeTask(
                fundsCommand(taskUid, taskId, 3, fixture, withdrawalNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.WAITING,
                third.outcome());
        assertEquals(2, channel.submitCount);
        assertEquals(1, channel.queryCount);
        ReliableFundsTaskExecutorPort.Result fourth = service.executeTask(
                fundsCommand(
                        taskUid, taskId, 4, fixture,
                        "CANCEL_MERCHANT_TRANSFER", withdrawalNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                fourth.outcome());
        assertEquals(2, channel.submitCount,
                "historical cancellation tasks must never call cancel/submit");
        assertEquals(2, channel.queryCount,
                "historical cancellation tasks must observe the original bill");

        assertEquals("CHANNEL_PROCESSING|990|10|990|10|1",
                withdrawalFundsState(withdrawalNo));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM ops_reconciliation_issue issue_row
                WHERE issue_row.issue_code =
                      'FUNDS.MERCHANT_TRANSFER_EVIDENCE_MISMATCH'
                  AND issue_row.subject_type = 'WECHAT_TRANSFER'
                  AND issue_row.subject_stable_key = ?
                  AND issue_row.state = 'UNRESOLVED'
                """, Integer.class,
                "MT" + withdrawalNo.substring(2)));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM fund_wechat_transfer_observation observation_row
                JOIN fund_wechat_transfer transfer_row
                  ON transfer_row.id = observation_row.transfer_id
                JOIN fund_withdrawal_order withdrawal
                  ON withdrawal.id = transfer_row.withdrawal_order_id
                WHERE withdrawal.withdrawal_order_no = ?
                  AND observation_row.observation_type = 'QUERY'
                  AND observation_row.raw_channel_state = 'SUCCESS'
                  AND observation_row.amount_cent = 11
                """, Integer.class, withdrawalNo));

        ReliableFundsTaskExecutorPort.Result fifth = service.executeTask(
                fundsCommand(taskUid, taskId, 5, fixture, withdrawalNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                fifth.outcome());

        assertEquals(2, channel.submitCount);
        assertEquals(3, channel.queryCount);
        assertEquals("SUCCEEDED|990|0|990|0|0",
                withdrawalFundsState(withdrawalNo));
        assertEquals(2, jdbc.queryForObject("""
                SELECT COUNT(*) FROM fund_user_wallet_entry entry_row
                JOIN fund_withdrawal_order withdrawal
                  ON withdrawal.id = entry_row.withdrawal_order_id
                WHERE withdrawal.withdrawal_order_no = ?
                  AND entry_row.organization_user_uid = ?
                  AND entry_row.source_type = 'WITHDRAWAL_ORDER'
                  AND entry_row.source_no = ?
                """, Integer.class, withdrawalNo,
                fixture.userUid().toString(), withdrawalNo));
    }

    @Test
    void frozenRecipientBlocksInitialWechatTransferSubmission()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("ti");
        String organizationCode = code("oi");
        createEnabledTenant(platform, tenantCode);
        createAndActivateOrganization(
                platform, tenantCode, organizationCode,
                "Transfer identity recheck");
        WithdrawalCreationFixture fixture = seedWithdrawalCreationFixture(
                tenantCode, organizationCode);

        FundsIdentityAccessPort identity = mock(FundsIdentityAccessPort.class);
        CurrentMiniappIdentity actor = new CurrentMiniappIdentity(
                fixture.tenantId(), tenantCode, fixture.organizationId(),
                organizationCode, fixture.miniappId(), fixture.appid(),
                fixture.userId(), fixture.userUid(), UUID.randomUUID(),
                "identity-recheck-user");
        when(identity.currentMiniapp(true)).thenReturn(actor);
        FundsIdentityAccessService identityVerifier =
                new FundsIdentityAccessService(jdbc);
        when(identity.lockWithdrawalTransferIdentity(any()))
                .thenAnswer(invocation -> identityVerifier
                        .lockWithdrawalTransferIdentity(
                                invocation.getArgument(0)));
        AuditPort audit = mock(AuditPort.class);
        when(audit.append(any())).thenReturn(1L);
        ScriptedMerchantTransferChannel channel =
                new ScriptedMerchantTransferChannel();
        WithdrawalApplicationService service =
                new WithdrawalApplicationService(
                        jdbc, new FundsAccessService(jdbc, identity),
                        reliableFundsTasks,
                        mock(ReliableFundsAttemptBoundaryPort.class), channel,
                        new TransactionTemplate(transactionManager), audit,
                        fundsOperationalControl, fundsListCursorCodec,
                        "https://fake.invalid");

        UUID createUid = UUID.randomUUID();
        service.create(createUid, new CreateWithdrawalRequest("0.10"));
        String withdrawalNo = RechargeApplicationService.stableNo(
                "WD", createUid);
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        jdbc.update("""
                UPDATE fund_withdrawal_order
                SET business_state = 'READY_TO_SUBMIT', reviewed_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE withdrawal_order_no = ?
                """, now, now, withdrawalNo);
        String snapshot = "{\"withdrawalNo\":\""
                + withdrawalNo + "\"}";
        UUID taskUid = new TransactionTemplate(transactionManager).execute(
                status -> reliableFundsTasks.register(
                        new ReliableFundsTaskRegistrationPort
                                .ReliableFundsTaskRegistration(
                                fixture.tenantId(), fixture.organizationId(),
                                "SUBMIT_MERCHANT_TRANSFER",
                                "SUBMIT_MERCHANT_TRANSFER:" + withdrawalNo,
                                "WITHDRAWAL_ORDER", withdrawalNo, 1, snapshot,
                                RechargeApplicationService.sha256(snapshot),
                                500, null)));
        assertNotNull(taskUid);
        long taskId = jdbc.queryForObject("""
                SELECT id FROM ops_reliable_task WHERE task_uid = ?
                """, Long.class, taskUid.toString());
        jdbc.update("""
                UPDATE iam_organization_user
                SET status = 'FROZEN', frozen_at = ?,
                    auth_version = auth_version + 1,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ?
                """, now, now, fixture.userId());

        ReliableFundsTaskExecutorPort.Result result = service.executeTask(
                fundsCommand(taskUid, taskId, 1, fixture, withdrawalNo));

        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                result.outcome());
        assertEquals(0, channel.submitCount);
        assertEquals(0, jdbc.queryForObject("""
                SELECT COUNT(*) FROM fund_wechat_transfer transfer_row
                JOIN fund_withdrawal_order withdrawal
                  ON withdrawal.id = transfer_row.withdrawal_order_id
                WHERE withdrawal.withdrawal_order_no = ?
                """, Integer.class, withdrawalNo));
        assertEquals("READY_TO_SUBMIT|990|10|990|10|1",
                withdrawalFundsState(withdrawalNo));
    }

    @Test
    void oppositeTrustedTransferCallbackCreatesReconciliationIssue()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("tc");
        String organizationCode = code("oc");
        createEnabledTenant(platform, tenantCode);
        createAndActivateOrganization(
                platform, tenantCode, organizationCode,
                "Transfer callback terminal conflict");
        WithdrawalCreationFixture fixture = seedWithdrawalCreationFixture(
                tenantCode, organizationCode);

        FundsIdentityAccessPort identity = mock(FundsIdentityAccessPort.class);
        CurrentMiniappIdentity actor = new CurrentMiniappIdentity(
                fixture.tenantId(), tenantCode, fixture.organizationId(),
                organizationCode, fixture.miniappId(), fixture.appid(),
                fixture.userId(), fixture.userUid(), UUID.randomUUID(),
                "callback-conflict-user");
        when(identity.currentMiniapp(true)).thenReturn(actor);
        when(identity.lockWithdrawalTransferIdentity(any()))
                .thenReturn(true);
        AuditPort audit = mock(AuditPort.class);
        when(audit.append(any())).thenReturn(1L);
        FailingMerchantTransferChannel channel =
                new FailingMerchantTransferChannel();
        WithdrawalApplicationService service =
                new WithdrawalApplicationService(
                        jdbc, new FundsAccessService(jdbc, identity),
                        reliableFundsTasks,
                        mock(ReliableFundsAttemptBoundaryPort.class), channel,
                        new TransactionTemplate(transactionManager), audit,
                        fundsOperationalControl, fundsListCursorCodec,
                        "https://fake.invalid");

        UUID createUid = UUID.randomUUID();
        service.create(createUid, new CreateWithdrawalRequest("0.10"));
        String withdrawalNo = RechargeApplicationService.stableNo(
                "WD", createUid);
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        jdbc.update("""
                UPDATE fund_withdrawal_order
                SET business_state = 'READY_TO_SUBMIT', reviewed_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE withdrawal_order_no = ?
                """, now, now, withdrawalNo);
        String snapshot = "{\"withdrawalNo\":\""
                + withdrawalNo + "\"}";
        UUID taskUid = new TransactionTemplate(transactionManager).execute(
                status -> reliableFundsTasks.register(
                        new ReliableFundsTaskRegistrationPort
                                .ReliableFundsTaskRegistration(
                                fixture.tenantId(), fixture.organizationId(),
                                "SUBMIT_MERCHANT_TRANSFER",
                                "SUBMIT_MERCHANT_TRANSFER:" + withdrawalNo,
                                "WITHDRAWAL_ORDER", withdrawalNo, 1, snapshot,
                                RechargeApplicationService.sha256(snapshot),
                                500, null)));
        assertNotNull(taskUid);
        long taskId = jdbc.queryForObject("""
                SELECT id FROM ops_reliable_task WHERE task_uid = ?
                """, Long.class, taskUid.toString());
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.WAITING,
                service.executeTask(fundsCommand(
                        taskUid, taskId, 1, fixture, withdrawalNo)).outcome());
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                service.executeTask(fundsCommand(
                        taskUid, taskId, 2, fixture, withdrawalNo)).outcome());
        assertEquals("CHANNEL_FAILED|1000|0|1000|0|0",
                withdrawalFundsState(withdrawalNo));

        JsonNode lateSuccess = objectMapper.valueToTree(Map.of(
                "out_bill_no", channel.request.outBillNo(),
                "state", "SUCCESS",
                "mch_id", channel.request.mchid(),
                "transfer_bill_no", channel.transferBillNo(),
                "openid", channel.request.openid(),
                "transfer_amount", channel.request.amountCent(),
                "update_time", Instant.now().toString()));
        long sourceInboxId = insertSyntheticWechatInbox(
                fixture, "WECHAT_TRANSFER_NOTIFICATION",
                lateSuccess.toString());
        ReliableFundsTaskExecutorPort.Command callbackSource = fundsCommand(
                taskUid, taskId, 3, fixture, withdrawalNo);
        Boolean applied = new TransactionTemplate(transactionManager).execute(
                status -> service.applyTrustedNotification(
                        sourceInboxId,
                        callbackSource.sourceTaskAttemptId(),
                        fixture.tenantId(), fixture.organizationId(),
                        lateSuccess));

        assertTrue(Boolean.TRUE.equals(applied));
        assertEquals("CHANNEL_FAILED|1000|0|1000|0|0",
                withdrawalFundsState(withdrawalNo));
        assertEquals(1, jdbc.queryForObject("""
                SELECT state_conflict FROM fund_wechat_transfer transfer_row
                JOIN fund_withdrawal_order withdrawal
                  ON withdrawal.id = transfer_row.withdrawal_order_id
                WHERE withdrawal.withdrawal_order_no = ?
                """, Integer.class, withdrawalNo));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM ops_reconciliation_issue
                WHERE issue_code =
                      'FUNDS.MERCHANT_TRANSFER_TERMINAL_CONFLICT'
                  AND subject_type = 'WECHAT_TRANSFER'
                  AND subject_stable_key = ?
                  AND state = 'UNRESOLVED'
                """, Integer.class, channel.request.outBillNo()));
        assertEquals(2, jdbc.queryForObject("""
                SELECT COUNT(*) FROM fund_user_wallet_entry entry_row
                JOIN fund_withdrawal_order withdrawal
                  ON withdrawal.id = entry_row.withdrawal_order_id
                WHERE withdrawal.withdrawal_order_no = ?
                """, Integer.class, withdrawalNo));
    }

    @Test
    void manualNotFoundQueryReopensExactBlockedSubmitTask()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("tq");
        String organizationCode = code("oq");
        createEnabledTenant(platform, tenantCode);
        createAndActivateOrganization(
                platform, tenantCode, organizationCode,
                "Manual transfer query recovery");
        WithdrawalCreationFixture fixture = seedWithdrawalCreationFixture(
                tenantCode, organizationCode);

        FundsIdentityAccessPort identity = mock(FundsIdentityAccessPort.class);
        CurrentMiniappIdentity actor = new CurrentMiniappIdentity(
                fixture.tenantId(), tenantCode, fixture.organizationId(),
                organizationCode, fixture.miniappId(), fixture.appid(),
                fixture.userId(), fixture.userUid(), UUID.randomUUID(),
                "manual-query-user");
        when(identity.currentMiniapp(true)).thenReturn(actor);
        when(identity.lockWithdrawalTransferIdentity(any()))
                .thenReturn(true);
        when(identity.authorizeWeb(
                false, organizationCode, "withdrawal.handle", false))
                .thenReturn(new AuthorizedWebIdentity(
                        false, tenantCode, fixture.userId(),
                        UUID.randomUUID(), UUID.randomUUID(),
                        "manual-query-staff"));
        AuditPort audit = mock(AuditPort.class);
        when(audit.append(any())).thenReturn(1L);
        ScriptedMerchantTransferChannel channel =
                new ScriptedMerchantTransferChannel();
        WithdrawalApplicationService service =
                new WithdrawalApplicationService(
                        jdbc, new FundsAccessService(jdbc, identity),
                        reliableFundsTasks,
                        mock(ReliableFundsAttemptBoundaryPort.class), channel,
                        new TransactionTemplate(transactionManager), audit,
                        fundsOperationalControl, fundsListCursorCodec,
                        "https://fake.invalid");

        UUID createUid = UUID.randomUUID();
        service.create(createUid, new CreateWithdrawalRequest("0.10"));
        String withdrawalNo = RechargeApplicationService.stableNo(
                "WD", createUid);
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        jdbc.update("""
                UPDATE fund_withdrawal_order
                SET business_state = 'READY_TO_SUBMIT', reviewed_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE withdrawal_order_no = ?
                """, now, now, withdrawalNo);
        String snapshot = "{\"withdrawalNo\":\""
                + withdrawalNo + "\"}";
        UUID submitTaskUid = new TransactionTemplate(transactionManager)
                .execute(status -> reliableFundsTasks.register(
                        new ReliableFundsTaskRegistrationPort
                                .ReliableFundsTaskRegistration(
                                fixture.tenantId(), fixture.organizationId(),
                                "SUBMIT_MERCHANT_TRANSFER",
                                "SUBMIT_MERCHANT_TRANSFER:" + withdrawalNo,
                                "WITHDRAWAL_ORDER", withdrawalNo, 1, snapshot,
                                RechargeApplicationService.sha256(snapshot),
                                500, null)));
        assertNotNull(submitTaskUid);
        long submitTaskId = jdbc.queryForObject("""
                SELECT id FROM ops_reliable_task WHERE task_uid = ?
                """, Long.class, submitTaskUid.toString());
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.RETRY,
                service.executeTask(fundsCommand(
                        submitTaskUid, submitTaskId, 1,
                        fixture, withdrawalNo)).outcome());
        LocalDateTime blockedAt = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        jdbc.update("""
                UPDATE ops_reliable_task
                SET state = 'BLOCKED', next_run_at = NULL,
                    completed_at = ?, blocked_reason_code = 'DATA_ERROR',
                    blocked_diagnostic = 'manual recovery fixture',
                    handled_wake_version = wake_version,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ?
                """, blockedAt, blockedAt, submitTaskId);
        long withdrawalVersion = jdbc.queryForObject("""
                SELECT lock_version FROM fund_withdrawal_order
                WHERE withdrawal_order_no = ?
                """, Long.class, withdrawalNo);
        new TransactionTemplate(transactionManager).executeWithoutResult(
                status -> service.requestChannelAction(
                        false, null, organizationCode, withdrawalNo,
                        UUID.randomUUID(),
                        new VersionedWithdrawalRequest(withdrawalVersion)));
        FundsTaskRef queryTask = fundsTask(
                "QUERY_MERCHANT_TRANSFER", withdrawalNo);

        ReliableFundsTaskExecutorPort.Result observed = service.executeTask(
                fundsCommand(
                        queryTask.taskUid(), queryTask.taskId(), 1,
                        fixture, "QUERY_MERCHANT_TRANSFER", withdrawalNo));

        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                observed.outcome());
        assertEquals("PENDING|NULL|NULL", jdbc.queryForObject("""
                SELECT CONCAT(state, '|',
                              COALESCE(completed_at, 'NULL'), '|',
                              COALESCE(blocked_reason_code, 'NULL'))
                FROM ops_reliable_task WHERE id = ?
                """, String.class, submitTaskId));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM ops_reliable_task
                WHERE task_type = 'QUERY_MERCHANT_TRANSFER'
                  AND target_type = 'WITHDRAWAL_ORDER'
                  AND target_stable_key = ?
                """, Integer.class, withdrawalNo));
    }

    @Test
    void nativeRechargeRejectsAmountMismatchBeforeTrustedNetPosting()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("nr");
        String organizationCode = code("no");
        createEnabledTenant(platform, tenantCode);
        createAndActivateOrganization(
                platform, tenantCode, organizationCode,
                "Native recharge evidence state machine");
        WithdrawalCreationFixture fixture = seedWithdrawalCreationFixture(
                tenantCode, organizationCode);
        long staffId = jdbc.queryForObject("""
                SELECT staff.id
                FROM iam_staff_account staff
                JOIN iam_tenant tenant ON tenant.id = staff.tenant_id
                WHERE tenant.tenant_code = ?
                ORDER BY staff.id
                LIMIT 1
                """, Long.class, tenantCode);

        FundsIdentityAccessPort identity = mock(FundsIdentityAccessPort.class);
        when(identity.authorizeWeb(
                false, organizationCode, "recharge.create", true))
                .thenReturn(new AuthorizedWebIdentity(
                        false, tenantCode, staffId, UUID.randomUUID(),
                        UUID.randomUUID(), "recharge-test-staff"));
        when(identity.authorizeWeb(
                false, organizationCode, "fund.read", false))
                .thenReturn(new AuthorizedWebIdentity(
                        false, tenantCode, staffId, UUID.randomUUID(),
                        UUID.randomUUID(), "recharge-test-staff"));
        ScriptedNativePaymentChannel channel =
                new ScriptedNativePaymentChannel();
        RechargeApplicationService creator = new RechargeApplicationService(
                jdbc, new FundsAccessService(jdbc, identity),
                reliableFundsTasks, channel, fundsOperationalControl,
                mock(ReliableFundsAttemptBoundaryPort.class),
                new TransactionTemplate(transactionManager),
                fundsListCursorCodec,
                "https://original.example");

        UUID operationUid = UUID.randomUUID();
        new TransactionTemplate(transactionManager).executeWithoutResult(
                status -> creator.create(
                        false, null, organizationCode, operationUid,
                        "1.00", "/api/v1/web/recharges"));
        UUID secondOperationUid = UUID.randomUUID();
        new TransactionTemplate(transactionManager).executeWithoutResult(
                status -> creator.create(
                        false, null, organizationCode, secondOperationUid,
                        "2.00", "/api/v1/web/recharges"));
        var firstPage = creator.list(
                false, null, organizationCode, null, null, 1,
                "/api/v1/web/recharges");
        assertEquals(1, firstPage.items().size());
        assertNotNull(firstPage.nextCursor());
        var secondPage = creator.list(
                false, null, organizationCode, null,
                firstPage.nextCursor(), 1,
                "/api/v1/web/recharges");
        assertEquals(1, secondPage.items().size());
        assertFalse(firstPage.items().getFirst().rechargeNo().equals(
                secondPage.items().getFirst().rechargeNo()));
        assertEquals(null, secondPage.nextCursor());
        TargetApiException cursorMismatch = assertThrows(
                TargetApiException.class,
                () -> creator.list(
                        false, null, organizationCode,
                        "PENDING_PAYMENT", firstPage.nextCursor(), 1,
                        "/api/v1/web/recharges"));
        assertEquals("COMMON.INVALID_CURSOR", cursorMismatch.code());

        String secondRechargeNo = RechargeApplicationService.stableNo(
                "RC", secondOperationUid);
        PaymentNotificationFixture callback = jdbc.queryForObject("""
                SELECT payment.mchid_snapshot, payment.appid_snapshot,
                       payment.out_trade_no, payment.request_amount_cent
                FROM fund_wechat_payment payment
                JOIN fund_recharge_order recharge
                  ON recharge.id = payment.recharge_order_id
                WHERE recharge.recharge_order_no = ?
                """, (rs, ignored) -> new PaymentNotificationFixture(
                        rs.getString("mchid_snapshot"),
                        rs.getString("appid_snapshot"),
                        rs.getString("out_trade_no"),
                        rs.getLong("request_amount_cent")),
                secondRechargeNo);
        LocalDateTime callbackNow = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        jdbc.update("""
                UPDATE fund_recharge_order
                SET business_state = 'EXPIRED', closed_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE recharge_order_no = ?
                """, callbackNow, callbackNow, secondRechargeNo);
        JsonNode lateSuccess = objectMapper.valueToTree(Map.of(
                "mchid", callback.mchid(),
                "appid", callback.appid(),
                "out_trade_no", callback.outTradeNo(),
                "trade_state", "SUCCESS",
                "transaction_id", "WXLATE" + callback.outTradeNo(),
                "success_time", Instant.now().toString(),
                "amount", Map.of(
                        "total", callback.amountCent(),
                        "payer_total", callback.amountCent()),
                "payer", Map.of("openid", "late-payer-" + run)));
        long sourceInboxId = insertSyntheticWechatInbox(
                fixture, "WECHAT_PAYMENT_NOTIFICATION",
                lateSuccess.toString());
        FundsTaskRef secondCreateTask = fundsTask(
                "CREATE_NATIVE_PAYMENT", secondRechargeNo);
        ReliableFundsTaskExecutorPort.Command callbackSource = fundsCommand(
                secondCreateTask.taskUid(), secondCreateTask.taskId(), 1,
                fixture, "CREATE_NATIVE_PAYMENT", secondRechargeNo);
        Boolean callbackApplied = new TransactionTemplate(transactionManager)
                .execute(status -> creator.applyTrustedNotification(
                        sourceInboxId,
                        callbackSource.sourceTaskAttemptId(),
                        fixture.tenantId(), fixture.organizationId(),
                        lateSuccess));
        assertTrue(Boolean.TRUE.equals(callbackApplied));
        assertEquals("EXPIRED|1000", jdbc.queryForObject("""
                SELECT CONCAT(recharge.business_state, '|',
                              account.available_payout_cent)
                FROM fund_recharge_order recharge
                JOIN fund_organization_payout_account account
                  ON account.tenant_id = recharge.tenant_id
                 AND account.organization_id = recharge.organization_id
                WHERE recharge.recharge_order_no = ?
                """, String.class, secondRechargeNo));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM ops_reconciliation_issue
                WHERE issue_code =
                      'FUNDS.NATIVE_PAYMENT_TERMINAL_CONFLICT'
                  AND subject_type = 'WECHAT_PAYMENT'
                  AND subject_stable_key = ?
                  AND state = 'UNRESOLVED'
                """, Integer.class, callback.outTradeNo()));
        RechargeApplicationService service = new RechargeApplicationService(
                jdbc, new FundsAccessService(jdbc, identity),
                reliableFundsTasks, channel, fundsOperationalControl,
                mock(ReliableFundsAttemptBoundaryPort.class),
                new TransactionTemplate(transactionManager),
                fundsListCursorCodec,
                "https://changed.example");
        String rechargeNo = RechargeApplicationService.stableNo(
                "RC", operationUid);
        FundsTaskRef createTask = fundsTask(
                "CREATE_NATIVE_PAYMENT", rechargeNo);
        ReliableFundsTaskExecutorPort.Result created = service.executeTask(
                fundsCommand(createTask.taskUid(), createTask.taskId(), 1,
                        fixture, "CREATE_NATIVE_PAYMENT", rechargeNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                created.outcome());
        assertEquals(
                "https://original.example/api/v1/wechat-pay/notifications/native-payments",
                channel.request.notifyUrl(),
                "worker must reuse the immutable URL snapshot after restart");

        FundsTaskRef queryTask = fundsTask(
                "QUERY_NATIVE_PAYMENT", rechargeNo);
        ReliableFundsTaskExecutorPort.Result mismatched = service.executeTask(
                fundsCommand(queryTask.taskUid(), queryTask.taskId(), 1,
                        fixture, "QUERY_NATIVE_PAYMENT", rechargeNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                mismatched.outcome());
        assertEquals("PENDING_PAYMENT|1000", jdbc.queryForObject("""
                SELECT CONCAT(recharge.business_state, '|',
                              account.available_payout_cent)
                FROM fund_recharge_order recharge
                JOIN fund_organization_payout_account account
                  ON account.tenant_id = recharge.tenant_id
                 AND account.organization_id = recharge.organization_id
                WHERE recharge.recharge_order_no = ?
                """, String.class, rechargeNo));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM fund_wechat_payment_observation observation_row
                JOIN fund_wechat_payment payment
                  ON payment.id = observation_row.payment_id
                JOIN fund_recharge_order recharge
                  ON recharge.id = payment.recharge_order_id
                WHERE recharge.recharge_order_no = ?
                  AND observation_row.observation_type = 'QUERY'
                  AND observation_row.total_amount_cent = 101
                  AND observation_row.observed_mchid = ?
                  AND observation_row.observed_appid = ?
                  AND observation_row.observed_out_trade_no = ?
                """, Integer.class, rechargeNo, channel.request.mchid(),
                channel.request.appid(), channel.request.outTradeNo()));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM ops_reconciliation_issue issue_row
                WHERE issue_row.issue_code =
                      'FUNDS.NATIVE_PAYMENT_EVIDENCE_MISMATCH'
                  AND issue_row.subject_type = 'WECHAT_PAYMENT'
                  AND issue_row.subject_stable_key = ?
                  AND issue_row.state = 'UNRESOLVED'
                """, Integer.class, channel.request.outTradeNo()));

        ReliableFundsTaskExecutorPort.Result paid = service.executeTask(
                fundsCommand(queryTask.taskUid(), queryTask.taskId(), 2,
                        fixture, "QUERY_NATIVE_PAYMENT", rechargeNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                paid.outcome());
        FundsTaskRef postTask = fundsTask(
                "POST_RECHARGE_NET_AMOUNT", rechargeNo);
        ReliableFundsTaskExecutorPort.Result posted = service.executeTask(
                fundsCommand(postTask.taskUid(), postTask.taskId(), 1,
                        fixture, "POST_RECHARGE_NET_AMOUNT", rechargeNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                posted.outcome());
        assertEquals("POSTED|1099|0", jdbc.queryForObject("""
                SELECT CONCAT(recharge.business_state, '|',
                              account.available_payout_cent, '|',
                              account.frozen_withdrawal_cent)
                FROM fund_recharge_order recharge
                JOIN fund_organization_payout_account account
                  ON account.tenant_id = recharge.tenant_id
                 AND account.organization_id = recharge.organization_id
                WHERE recharge.recharge_order_no = ?
                """, String.class, rechargeNo));
        assertEquals(1, channel.createCount);
        assertEquals(2, channel.queryCount);

        UUID legacyOperation = UUID.randomUUID();
        new TransactionTemplate(transactionManager).executeWithoutResult(
                status -> creator.create(
                        false, null, organizationCode, legacyOperation,
                        "1.00", "/api/v1/web/recharges"));
        String legacyRechargeNo = RechargeApplicationService.stableNo(
                "RC", legacyOperation);
        jdbc.update("""
                UPDATE fund_wechat_payment payment
                JOIN fund_recharge_order recharge
                  ON recharge.id = payment.recharge_order_id
                SET payment.notify_url_snapshot = NULL
                WHERE recharge.recharge_order_no = ?
                """, legacyRechargeNo);
        FundsTaskRef legacyCreate = fundsTask(
                "CREATE_NATIVE_PAYMENT", legacyRechargeNo);
        ReliableFundsTaskExecutorPort.Result legacyBlocked =
                service.executeTask(fundsCommand(
                        legacyCreate.taskUid(), legacyCreate.taskId(), 1,
                        fixture, "CREATE_NATIVE_PAYMENT", legacyRechargeNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                legacyBlocked.outcome());
        assertEquals(1, channel.createCount,
                "an unverifiable legacy URL must be blocked before WeChat");
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM ops_reconciliation_issue issue_row
                JOIN fund_wechat_payment payment
                  ON payment.out_trade_no = issue_row.subject_stable_key
                JOIN fund_recharge_order recharge
                  ON recharge.id = payment.recharge_order_id
                WHERE recharge.recharge_order_no = ?
                  AND issue_row.issue_code =
                      'FUNDS.NATIVE_PAYMENT_ORIGINAL_REQUEST_UNAVAILABLE'
                """, Integer.class, legacyRechargeNo));
    }

    @Test
    void authorizedWalletAdjustmentAppendsLedgerAndControlsSafetyGates()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("wa");
        String organizationCode = code("wo");
        createEnabledTenant(platform, tenantCode);
        createAndActivateOrganization(
                platform, tenantCode, organizationCode,
                "Wallet adjustment integration");
        WithdrawalCreationFixture fixture = seedWithdrawalCreationFixture(
                tenantCode, organizationCode);
        String base = "/api/v1/web/platform/tenants/" + tenantCode
                + "/organizations/" + organizationCode
                + "/organization-users/" + fixture.userUid();

        JsonNode wallet = data(read(platform, base + "/wallet", 200));
        assertEquals("10.00", wallet.path("availableBalanceYuan").asText());
        assertEquals(0, wallet.path("walletVersion").asLong());

        UUID debitUid = UUID.randomUUID();
        Map<String, Object> debit = Map.of(
                "deltaYuan", "-21.00",
                "expectedWalletVersion", 0,
                "reason", "纠正重复返现");
        JsonNode debited = data(write(
                platform,
                post(base + "/wallet-adjustments"),
                debitUid,
                debit,
                201));
        assertEquals(debitUid.toString(),
                debited.path("adjustmentUid").asText());
        assertEquals("10.00",
                debited.path("availableBalanceBeforeYuan").asText());
        assertEquals("-11.00",
                debited.path("availableBalanceAfterYuan").asText());
        assertEquals("MANUAL_RECOVERY_REQUIRED",
                debited.path("deliveryGate").asText());
        assertEquals("NONE",
                debited.path("activeWithdrawalEffect").asText());
        assertEquals(1, debited.path("walletVersion").asLong());

        JsonNode replay = data(write(
                platform,
                post(base + "/wallet-adjustments"),
                debitUid,
                debit,
                201));
        assertEquals(debited, replay);
        write(
                platform,
                post(base + "/wallet-adjustments"),
                debitUid,
                Map.of(
                        "deltaYuan", "-20.00",
                        "expectedWalletVersion", 0,
                        "reason", "纠正重复返现"),
                409);

        JsonNode restored = data(write(
                platform,
                post(base + "/wallet-adjustments"),
                UUID.randomUUID(),
                Map.of(
                        "deltaYuan", "2.01",
                        "expectedWalletVersion", 1),
                201));
        assertEquals("-8.99",
                restored.path("availableBalanceAfterYuan").asText());
        assertEquals("OPEN", restored.path("deliveryGate").asText());
        assertEquals(2, restored.path("walletVersion").asLong());

        assertEquals("-899|0|2|OPEN|NULL|NULL", jdbc.queryForObject("""
                SELECT CONCAT(
                    available_balance_cent, '|', frozen_withdrawal_cent, '|',
                    last_entry_sequence_no, '|', delivery_gate_state, '|',
                    COALESCE(delivery_gate_threshold_snapshot_cent, 'NULL'),
                    '|', COALESCE(delivery_gate_trigger_entry_id, 'NULL'))
                FROM fund_user_wallet
                WHERE tenant_id = ? AND organization_id = ?
                  AND organization_user_id = ?
                """, String.class, fixture.tenantId(),
                fixture.organizationId(), fixture.userId()));
        assertEquals(2, jdbc.queryForObject("""
                SELECT COUNT(*) FROM fund_wallet_adjustment
                WHERE tenant_id = ? AND organization_id = ?
                  AND wallet_id = (
                    SELECT id FROM fund_user_wallet
                    WHERE tenant_id = ? AND organization_id = ?
                      AND organization_user_id = ?)
                """, Integer.class,
                fixture.tenantId(), fixture.organizationId(),
                fixture.tenantId(), fixture.organizationId(), fixture.userId()));
        assertEquals(2, jdbc.queryForObject("""
                SELECT COUNT(*) FROM fund_user_wallet_entry
                WHERE tenant_id = ? AND organization_id = ?
                  AND organization_user_id = ?
                  AND event_type = 'MANUAL_ADJUSTMENT'
                  AND frozen_delta_cent = 0
                  AND frozen_before_cent = frozen_after_cent
                """, Integer.class, fixture.tenantId(),
                fixture.organizationId(), fixture.userId()));
        assertEquals(2, jdbc.queryForObject("""
                SELECT COUNT(*) FROM ops_audit_log
                WHERE tenant_id = ? AND organization_id = ?
                  AND action_code = 'wallet.adjust'
                  AND result = 'SUCCEEDED'
                """, Integer.class,
                fixture.tenantId(), fixture.organizationId()));
    }

    @Test
    void walletAdjustmentPausesAndWakesPreChannelWithdrawal()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("wp");
        String organizationCode = code("wr");
        createEnabledTenant(platform, tenantCode);
        createAndActivateOrganization(
                platform, tenantCode, organizationCode,
                "Wallet adjustment withdrawal recovery");
        WithdrawalCreationFixture fixture = seedWithdrawalCreationFixture(
                tenantCode, organizationCode);

        FundsIdentityAccessPort identity = mock(FundsIdentityAccessPort.class);
        CurrentMiniappIdentity actor = new CurrentMiniappIdentity(
                fixture.tenantId(), tenantCode, fixture.organizationId(),
                organizationCode, fixture.miniappId(), fixture.appid(),
                fixture.userId(), fixture.userUid(), UUID.randomUUID(),
                "wallet-adjustment-user");
        when(identity.currentMiniapp(true)).thenReturn(actor);
        AuditPort audit = mock(AuditPort.class);
        when(audit.append(any())).thenReturn(1L);
        WithdrawalApplicationService withdrawalService =
                new WithdrawalApplicationService(
                        jdbc, new FundsAccessService(jdbc, identity),
                        reliableFundsTasks,
                        mock(ReliableFundsAttemptBoundaryPort.class),
                        mock(MerchantTransferChannelPort.class),
                        new TransactionTemplate(transactionManager), audit,
                        fundsOperationalControl, fundsListCursorCodec,
                        "https://fake.invalid");

        UUID createUid = UUID.randomUUID();
        withdrawalService.create(
                createUid, new CreateWithdrawalRequest("0.10"));
        String withdrawalNo = RechargeApplicationService.stableNo(
                "WD", createUid);
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        assertEquals(1, jdbc.update("""
                UPDATE fund_withdrawal_order
                SET business_state = 'READY_TO_SUBMIT', reviewed_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE withdrawal_order_no = ?
                  AND business_state = 'PENDING_REVIEW'
                """, now, now, withdrawalNo));
        String taskSnapshot = "{\"withdrawalNo\":\""
                + withdrawalNo + "\"}";
        UUID taskUid = new TransactionTemplate(transactionManager).execute(
                status -> reliableFundsTasks.register(
                        new ReliableFundsTaskRegistrationPort
                                .ReliableFundsTaskRegistration(
                                fixture.tenantId(), fixture.organizationId(),
                                "SUBMIT_MERCHANT_TRANSFER",
                                "SUBMIT_MERCHANT_TRANSFER:" + withdrawalNo,
                                "WITHDRAWAL_ORDER", withdrawalNo, 1,
                                taskSnapshot,
                                RechargeApplicationService.sha256(
                                        taskSnapshot),
                                500, null)));
        assertNotNull(taskUid);
        jdbc.update("""
                UPDATE ops_reliable_task
                SET next_run_at = UTC_TIMESTAMP(3) + INTERVAL 1 DAY
                WHERE task_uid = ?
                """, taskUid.toString());

        String base = "/api/v1/web/platform/tenants/" + tenantCode
                + "/organizations/" + organizationCode
                + "/organization-users/" + fixture.userUid();
        JsonNode paused = data(write(
                platform,
                post(base + "/wallet-adjustments"),
                UUID.randomUUID(),
                Map.of(
                        "deltaYuan", "-10.00",
                        "expectedWalletVersion", 1,
                        "reason", "纠正活动提现前余额"),
                201));
        assertEquals("-0.10",
                paused.path("availableBalanceAfterYuan").asText());
        assertEquals("PAUSED_BEFORE_CHANNEL",
                paused.path("activeWithdrawalEffect").asText());
        assertEquals("1|0", jdbc.queryForObject("""
                SELECT CONCAT(negative_balance_pause, '|',
                              post_boundary_risk)
                FROM fund_withdrawal_order
                WHERE withdrawal_order_no = ?
                """, String.class, withdrawalNo));

        JsonNode resumed = data(write(
                platform,
                post(base + "/wallet-adjustments"),
                UUID.randomUUID(),
                Map.of(
                        "deltaYuan", "0.10",
                        "expectedWalletVersion", 2,
                        "reason", "恢复活动提现前余额"),
                201));
        assertEquals("0.00",
                resumed.path("availableBalanceAfterYuan").asText());
        assertEquals("RESUMED_BEFORE_CHANNEL",
                resumed.path("activeWithdrawalEffect").asText());
        assertEquals("0|0|0|10|3", jdbc.queryForObject("""
                SELECT CONCAT(
                    withdrawal.negative_balance_pause, '|',
                    withdrawal.post_boundary_risk, '|',
                    wallet.available_balance_cent, '|',
                    wallet.frozen_withdrawal_cent, '|',
                    wallet.lock_version)
                FROM fund_withdrawal_order withdrawal
                JOIN fund_user_wallet wallet
                  ON wallet.id = withdrawal.wallet_id
                WHERE withdrawal.withdrawal_order_no = ?
                """, String.class, withdrawalNo));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM ops_reliable_task
                WHERE task_uid = ?
                  AND state = 'PENDING'
                  AND wake_version = 1
                  AND next_run_at <= UTC_TIMESTAMP(3)
                        + INTERVAL 2 SECOND
                """, Integer.class, taskUid.toString()));

        JsonNode pausedBeforeBlockedTask = data(write(
                platform,
                post(base + "/wallet-adjustments"),
                UUID.randomUUID(),
                Map.of(
                        "deltaYuan", "-0.01",
                        "expectedWalletVersion", 3,
                        "reason", "再次验证阻断任务恢复边界"),
                201));
        assertEquals("PAUSED_BEFORE_CHANNEL",
                pausedBeforeBlockedTask.path(
                        "activeWithdrawalEffect").asText());
        assertEquals(1, jdbc.update("""
                UPDATE ops_reliable_task
                SET state = 'BLOCKED', next_run_at = NULL,
                    lease_token = NULL, lease_worker = NULL,
                    lease_until = NULL,
                    completed_at = UTC_TIMESTAMP(3),
                    blocked_reason_code = 'DATA_ERROR',
                    blocked_diagnostic = 'requires precise recovery',
                    handled_wake_version = wake_version,
                    updated_at = UTC_TIMESTAMP(3)
                WHERE task_uid = ? AND state = 'PENDING'
                """, taskUid.toString()));
        JsonNode recoveredWithBlockedTask = data(write(
                platform,
                post(base + "/wallet-adjustments"),
                UUID.randomUUID(),
                Map.of(
                        "deltaYuan", "0.01",
                        "expectedWalletVersion", 4,
                        "reason", "余额恢复但任务仍需精确处置"),
                201));
        assertEquals("PAUSE_CLEARED_TASK_NOT_WAKEABLE",
                recoveredWithBlockedTask.path(
                        "activeWithdrawalEffect").asText());
        assertEquals("0|BLOCKED|1|DATA_ERROR",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            withdrawal.negative_balance_pause, '|',
                            task.state, '|', task.wake_version, '|',
                            task.blocked_reason_code)
                        FROM fund_withdrawal_order withdrawal
                        JOIN ops_reliable_task task
                          ON task.target_type = 'WITHDRAWAL_ORDER'
                         AND task.target_stable_key =
                             withdrawal.withdrawal_order_no
                        WHERE withdrawal.withdrawal_order_no = ?
                        """, String.class, withdrawalNo));

        String otherWaitReason = "PAYOUT_NOT_ENOUGH:"
                + UUID.randomUUID();
        assertEquals(1, jdbc.update("""
                UPDATE ops_reliable_task
                SET state = 'PENDING',
                    next_run_at = UTC_TIMESTAMP(3) + INTERVAL 1 DAY,
                    completed_at = NULL,
                    blocked_reason_code = NULL,
                    blocked_diagnostic = NULL,
                    dispatch_wait_reason = ?,
                    updated_at = UTC_TIMESTAMP(3)
                WHERE task_uid = ? AND state = 'BLOCKED'
                """, otherWaitReason, taskUid.toString()));
        data(write(
                platform,
                post(base + "/wallet-adjustments"),
                UUID.randomUUID(),
                Map.of(
                        "deltaYuan", "-0.01",
                        "expectedWalletVersion", 5,
                        "reason", "验证其他派发等待条件"),
                201));
        JsonNode recoveredStillWaiting = data(write(
                platform,
                post(base + "/wallet-adjustments"),
                UUID.randomUUID(),
                Map.of(
                        "deltaYuan", "0.01",
                        "expectedWalletVersion", 6,
                        "reason", "余额恢复但保留出款闸等待"),
                201));
        assertEquals("PAUSE_CLEARED_TASK_STILL_WAITING",
                recoveredStillWaiting.path(
                        "activeWithdrawalEffect").asText());
        assertEquals("PENDING|1|" + otherWaitReason,
                jdbc.queryForObject("""
                        SELECT CONCAT(state, '|', wake_version, '|',
                                      dispatch_wait_reason)
                        FROM ops_reliable_task WHERE task_uid = ?
                        """, String.class, taskUid.toString()));

        UUID leaseToken = UUID.randomUUID();
        assertEquals(1, jdbc.update("""
                UPDATE ops_reliable_task
                SET dispatch_wait_reason = NULL,
                    next_run_at = UTC_TIMESTAMP(3) + INTERVAL 1 DAY,
                    lease_token = ?, lease_worker = 'wallet-adjust-test',
                    lease_until = UTC_TIMESTAMP(3) + INTERVAL 1 HOUR,
                    updated_at = UTC_TIMESTAMP(3)
                WHERE task_uid = ? AND state = 'PENDING'
                """, leaseToken.toString(), taskUid.toString()));
        data(write(
                platform,
                post(base + "/wallet-adjustments"),
                UUID.randomUUID(),
                Map.of(
                        "deltaYuan", "-0.01",
                        "expectedWalletVersion", 7,
                        "reason", "验证租约中任务的唤醒"),
                201));
        JsonNode recoveredDuringLease = data(write(
                platform,
                post(base + "/wallet-adjustments"),
                UUID.randomUUID(),
                Map.of(
                        "deltaYuan", "0.01",
                        "expectedWalletVersion", 8,
                        "reason", "租约执行期间恢复余额"),
                201));
        assertEquals("RESUMED_BEFORE_CHANNEL",
                recoveredDuringLease.path(
                        "activeWithdrawalEffect").asText());
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM ops_reliable_task
                WHERE task_uid = ? AND state = 'PENDING'
                  AND wake_version = 2
                  AND lease_token = ?
                  AND next_run_at >= UTC_TIMESTAMP(3)
                        + INTERVAL 12 HOUR
                """, Integer.class,
                taskUid.toString(), leaseToken.toString()));
    }

    @Test
    void nativeDuplicateNumberQueriesOriginalAndRefundBlocksPosting()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("nd");
        String organizationCode = code("nf");
        createEnabledTenant(platform, tenantCode);
        createAndActivateOrganization(
                platform, tenantCode, organizationCode,
                "Native duplicate and refund recovery");
        WithdrawalCreationFixture fixture = seedWithdrawalCreationFixture(
                tenantCode, organizationCode);
        long staffId = jdbc.queryForObject("""
                SELECT staff.id
                FROM iam_staff_account staff
                JOIN iam_tenant tenant ON tenant.id = staff.tenant_id
                WHERE tenant.tenant_code = ?
                ORDER BY staff.id LIMIT 1
                """, Long.class, tenantCode);
        FundsIdentityAccessPort identity = mock(FundsIdentityAccessPort.class);
        when(identity.authorizeWeb(
                false, organizationCode, "recharge.create", true))
                .thenReturn(new AuthorizedWebIdentity(
                        false, tenantCode, staffId, UUID.randomUUID(),
                        UUID.randomUUID(), "native-recovery-staff"));
        DuplicateRefundNativeChannel channel =
                new DuplicateRefundNativeChannel();
        RechargeApplicationService service = new RechargeApplicationService(
                jdbc, new FundsAccessService(jdbc, identity),
                reliableFundsTasks, channel, fundsOperationalControl,
                mock(ReliableFundsAttemptBoundaryPort.class),
                new TransactionTemplate(transactionManager),
                fundsListCursorCodec,
                "https://native.example");

        UUID operationUid = UUID.randomUUID();
        new TransactionTemplate(transactionManager).executeWithoutResult(
                status -> service.create(
                        false, null, organizationCode, operationUid,
                        "1.00", "/api/v1/web/recharges"));
        String rechargeNo = RechargeApplicationService.stableNo(
                "RC", operationUid);
        FundsTaskRef createTask = fundsTask(
                "CREATE_NATIVE_PAYMENT", rechargeNo);
        ReliableFundsTaskExecutorPort.Result duplicate = service.executeTask(
                fundsCommand(createTask.taskUid(), createTask.taskId(), 1,
                        fixture, "CREATE_NATIVE_PAYMENT", rechargeNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                duplicate.outcome());
        assertEquals(1, channel.createCount);

        FundsTaskRef queryTask = fundsTask(
                "QUERY_NATIVE_PAYMENT", rechargeNo);
        ReliableFundsTaskExecutorPort.Result refunded = service.executeTask(
                fundsCommand(queryTask.taskUid(), queryTask.taskId(), 1,
                        fixture, "QUERY_NATIVE_PAYMENT", rechargeNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                refunded.outcome());
        assertEquals(1, channel.queryCount);
        assertEquals("PENDING_PAYMENT|1000", jdbc.queryForObject("""
                SELECT CONCAT(recharge.business_state, '|',
                              account.available_payout_cent)
                FROM fund_recharge_order recharge
                JOIN fund_organization_payout_account account
                  ON account.tenant_id = recharge.tenant_id
                 AND account.organization_id = recharge.organization_id
                WHERE recharge.recharge_order_no = ?
                """, String.class, rechargeNo));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM ops_reconciliation_issue
                WHERE issue_code = 'FUNDS.NATIVE_PAYMENT_REFUNDED'
                  AND subject_stable_key = ?
                  AND state = 'UNRESOLVED'
                """, Integer.class, channel.request.outTradeNo()));
    }

    @Test
    void nativeCloseConvergenceUsesThirtySecondBackoff()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("nc");
        String organizationCode = code("cl");
        createEnabledTenant(platform, tenantCode);
        createAndActivateOrganization(
                platform, tenantCode, organizationCode,
                "Native close convergence");
        WithdrawalCreationFixture fixture = seedWithdrawalCreationFixture(
                tenantCode, organizationCode);
        long staffId = jdbc.queryForObject("""
                SELECT staff.id
                FROM iam_staff_account staff
                JOIN iam_tenant tenant ON tenant.id = staff.tenant_id
                WHERE tenant.tenant_code = ?
                ORDER BY staff.id LIMIT 1
                """, Long.class, tenantCode);
        FundsIdentityAccessPort identity = mock(FundsIdentityAccessPort.class);
        when(identity.authorizeWeb(
                false, organizationCode, "recharge.create", true))
                .thenReturn(new AuthorizedWebIdentity(
                        false, tenantCode, staffId, UUID.randomUUID(),
                        UUID.randomUUID(), "native-close-staff"));
        ClosingNativeChannel channel = new ClosingNativeChannel();
        RechargeApplicationService service = new RechargeApplicationService(
                jdbc, new FundsAccessService(jdbc, identity),
                reliableFundsTasks, channel, fundsOperationalControl,
                mock(ReliableFundsAttemptBoundaryPort.class),
                new TransactionTemplate(transactionManager),
                fundsListCursorCodec,
                "https://native-close.example");

        UUID operationUid = UUID.randomUUID();
        new TransactionTemplate(transactionManager).executeWithoutResult(
                status -> service.create(
                        false, null, organizationCode, operationUid,
                        "1.00", "/api/v1/web/recharges"));
        String rechargeNo = RechargeApplicationService.stableNo(
                "RC", operationUid);
        FundsTaskRef createTask = fundsTask(
                "CREATE_NATIVE_PAYMENT", rechargeNo);
        ReliableFundsTaskExecutorPort.Result createRetry =
                service.executeTask(fundsCommand(
                        createTask.taskUid(), createTask.taskId(), 1,
                        fixture, "CREATE_NATIVE_PAYMENT", rechargeNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.RETRY,
                createRetry.outcome());
        assertEquals(Duration.ofSeconds(30), createRetry.retryAfter());
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                service.executeTask(fundsCommand(
                        createTask.taskUid(), createTask.taskId(), 2,
                        fixture, "CREATE_NATIVE_PAYMENT", rechargeNo))
                        .outcome());
        FundsTaskRef queryTask = fundsTask(
                "QUERY_NATIVE_PAYMENT", rechargeNo);
        ReliableFundsTaskExecutorPort.Result expiredQuery =
                new TransactionTemplate(transactionManager).execute(status -> {
                    jdbc.execute("SET SESSION timestamp = "
                            + "UNIX_TIMESTAMP() + 1860");
                    try {
                        return service.executeTask(fundsCommand(
                                queryTask.taskUid(), queryTask.taskId(), 1,
                                fixture, "QUERY_NATIVE_PAYMENT", rechargeNo));
                    } finally {
                        jdbc.execute("SET SESSION timestamp = DEFAULT");
                    }
                });
        assertNotNull(expiredQuery);
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                expiredQuery.outcome());
        FundsTaskRef closeTask = fundsTask(
                "CLOSE_NATIVE_PAYMENT", rechargeNo);
        ReliableFundsTaskExecutorPort.Result closeRetry =
                service.executeTask(fundsCommand(
                        closeTask.taskUid(), closeTask.taskId(), 1,
                        fixture, "CLOSE_NATIVE_PAYMENT", rechargeNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.RETRY,
                closeRetry.outcome());
        assertEquals(Duration.ofSeconds(30), closeRetry.retryAfter());
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                service.executeTask(fundsCommand(
                        closeTask.taskUid(), closeTask.taskId(), 2,
                        fixture, "CLOSE_NATIVE_PAYMENT", rechargeNo))
                        .outcome());

        ReliableFundsTaskExecutorPort.Result waiting = service.executeTask(
                fundsCommand(
                        queryTask.taskUid(), queryTask.taskId(), 2,
                        fixture, "QUERY_NATIVE_PAYMENT", rechargeNo));
        assertEquals(
                ReliableFundsTaskExecutorPort.Result.Outcome.WAITING,
                waiting.outcome());
        assertEquals(Duration.ofSeconds(30), waiting.retryAfter());
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM ops_reliable_task
                WHERE task_type = 'QUERY_NATIVE_PAYMENT'
                  AND task_key = ?
                  AND next_run_at >= UTC_TIMESTAMP(3)
                        + INTERVAL 25 SECOND
                """, Integer.class,
                ("QUERY_NATIVE_PAYMENT_AFTER_CLOSE:" + rechargeNo)
                        .toUpperCase(Locale.ROOT)));
    }

    private String withdrawalFundsState(String withdrawalNo) {
        return jdbc.queryForObject("""
                SELECT CONCAT(
                    withdrawal.business_state, '|',
                    wallet.available_balance_cent, '|',
                    wallet.frozen_withdrawal_cent, '|',
                    account.available_payout_cent, '|',
                    account.frozen_withdrawal_cent, '|',
                    COUNT(active.withdrawal_order_id)
                )
                FROM fund_withdrawal_order withdrawal
                JOIN fund_user_wallet wallet ON wallet.id = withdrawal.wallet_id
                JOIN fund_organization_payout_account account
                  ON account.id = withdrawal.organization_payout_account_id
                LEFT JOIN fund_active_withdrawal active
                  ON active.withdrawal_order_id = withdrawal.id
                WHERE withdrawal.withdrawal_order_no = ?
                GROUP BY withdrawal.id, withdrawal.business_state,
                         wallet.available_balance_cent,
                         wallet.frozen_withdrawal_cent,
                         account.available_payout_cent,
                         account.frozen_withdrawal_cent
                """, String.class, withdrawalNo);
    }

    private ReliableFundsTaskExecutorPort.Command fundsCommand(
            UUID taskUid,
            long taskId,
            long attemptNo,
            WithdrawalCreationFixture fixture,
            String withdrawalNo) {
        return fundsCommand(
                taskUid, taskId, attemptNo, fixture,
                "SUBMIT_MERCHANT_TRANSFER", withdrawalNo);
    }

    private ReliableFundsTaskExecutorPort.Command fundsCommand(
            UUID taskUid,
            long taskId,
            long attemptNo,
            WithdrawalCreationFixture fixture,
            String taskType,
            String targetStableKey) {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        UUID attemptUid = UUID.randomUUID();
        String action = taskType.startsWith("QUERY_")
                ? "QUERY"
                : taskType.startsWith("POST_") ? "PROCESS" : "SUBMIT";
        jdbc.update("""
                INSERT INTO ops_task_attempt (
                    attempt_uid, task_id, scope_kind, tenant_id,
                    organization_id, attempt_no, lease_token,
                    claimed_wake_version, worker_id, claimed_at,
                    lease_until, external_call_may_have_started_at,
                    reclaimed_at, result_recorded_at, action_kind,
                    technical_result, request_sha256, response_sha256,
                    http_status, external_api_error_code, duration_ms,
                    redacted_diagnostic, created_at
                ) VALUES (
                    ?, ?, 'ORGANIZATION', ?, ?, ?, ?, 0,
                    'funds-state-test', ?, ?, NULL, NULL, NULL, ?,
                    NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?
                )
                """, attemptUid.toString(), taskId, fixture.tenantId(),
                fixture.organizationId(), attemptNo,
                UUID.randomUUID().toString(), now, now.plusMinutes(5),
                action, now);
        long attemptId = jdbc.queryForObject("""
                SELECT id FROM ops_task_attempt WHERE attempt_uid = ?
                """, Long.class, attemptUid.toString());
        return new ReliableFundsTaskExecutorPort.Command(
                taskUid, attemptUid, attemptId,
                taskType, targetStableKey);
    }

    private long insertSyntheticWechatInbox(
            WithdrawalCreationFixture fixture,
            String messageKind,
            String normalizedPayload) {
        UUID inboxUid = UUID.randomUUID();
        String externalMessageId = UUID.randomUUID().toString();
        byte[] raw = normalizedPayload.getBytes(StandardCharsets.UTF_8);
        byte[] contentHash = RechargeApplicationService.sha256(
                normalizedPayload);
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        jdbc.update("""
                INSERT INTO ops_inbox_message (
                    inbox_uid, scope_kind, tenant_id, organization_id,
                    source_namespace, source_principal_key,
                    external_message_id, message_kind,
                    normalized_schema_version, raw_transport_body,
                    raw_transport_sha256, normalized_payload,
                    normalized_content_sha256, authentication_method,
                    authentication_principal_ref, correlation_uid,
                    causation_uid, processing_state, first_received_at,
                    last_received_at, delivery_count, processed_at,
                    lock_version, created_at, updated_at
                ) VALUES (
                    ?, 'ORGANIZATION', ?, ?, 'wechat_pay_test', ?, ?, ?,
                    1, ?, ?, CAST(? AS JSON), ?, 'WECHATPAY_SIGNATURE',
                    'synthetic-wechatpay-public-key', NULL, NULL, 'RECEIVED',
                    ?, ?, 1, NULL, 0, ?, ?
                )
                """, inboxUid.toString(), fixture.tenantId(),
                fixture.organizationId(), "merchant-" + run,
                externalMessageId, messageKind, raw, contentHash,
                normalizedPayload, contentHash, now, now, now, now);
        return jdbc.queryForObject("""
                SELECT id FROM ops_inbox_message WHERE inbox_uid = ?
                """, Long.class, inboxUid.toString());
    }

    private FundsTaskRef fundsTask(String taskType, String targetStableKey) {
        return jdbc.queryForObject("""
                SELECT id, task_uid
                FROM ops_reliable_task
                WHERE task_type = ? AND target_stable_key = ?
                """, (rs, ignored) -> new FundsTaskRef(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("task_uid"))),
                taskType, targetStableKey);
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
    void merchantBindingWritesObjectAuditAndSupportsReverification()
            throws Exception {
        BrowserClient platform = platformClient();
        String tenantCode = code("binding-t");
        String organizationCode = code("binding-o");
        createEnabledTenant(platform, tenantCode);
        createAndActivateOrganization(
                platform,
                tenantCode,
                organizationCode,
                "Merchant binding organization");
        String base = "/api/v1/web/platform/tenants/" + tenantCode
                + "/organizations/" + organizationCode;
        String appId = "wx" + UUID.randomUUID().toString()
                .replace("-", "").substring(0, 16);
        write(
                platform,
                put(base + "/miniapp-configuration"),
                UUID.randomUUID(),
                Map.of(
                        "appId", appId,
                        "displayName", "Binding miniapp",
                        "appSecret", "fake-binding-app-secret-" + run),
                200);
        write(
                platform,
                post(base + "/miniapp-configuration/activations"),
                UUID.randomUUID(),
                Map.of("expectedVersion", 0),
                200);
        write(
                platform,
                post(base + "/miniapp-login/enablements"),
                UUID.randomUUID(),
                Map.of("expectedVersion", 1),
                200);

        UUID nonVersionFourUid = UUID.fromString(
                "00000000-0000-1000-8000-000000000000");
        MvcResult invalidIdempotencyKey = write(
                platform,
                post(base + "/wechat-merchant-binding/verifications"),
                nonVersionFourUid,
                Map.of(
                        "expectedMiniappVersion", 2,
                        "note", "不应进入数据库约束"),
                400);
        assertEquals(
                "COMMON.VALIDATION_FAILED",
                json(invalidIdempotencyKey).path("code").asText());
        assertEquals(0, jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM fund_miniapp_merchant_binding b
                JOIN iam_tenant t ON t.id = b.tenant_id
                JOIN iam_organization o
                  ON o.tenant_id = b.tenant_id
                 AND o.id = b.organization_id
                WHERE t.tenant_code = ?
                  AND o.organization_code = ?
                """, Integer.class, tenantCode, organizationCode));
        assertEquals(0, jdbc.queryForObject("""
                SELECT COUNT(*) FROM ops_audit_log
                WHERE operation_uid = ?
                """, Integer.class, nonVersionFourUid.toString()));

        UUID verifyOperationUid = UUID.randomUUID();
        Map<String, Object> verifyRequest = Map.of(
                "expectedMiniappVersion", 2,
                "note", "已在微信商户平台核查");
        JsonNode verified = data(write(
                platform,
                post(base + "/wechat-merchant-binding/verifications"),
                verifyOperationUid,
                verifyRequest,
                200));
        assertEquals("VERIFIED", verified.path("status").asText());
        assertEquals(0, verified.path("bindingVersion").asLong());
        assertEquals(verified, data(write(
                platform,
                post(base + "/wechat-merchant-binding/verifications"),
                verifyOperationUid,
                verifyRequest,
                200)));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM ops_audit_log
                WHERE succeeded_operation_uid = ?
                """, Integer.class, verifyOperationUid.toString()));
        MvcResult changedVerifyReplay = write(
                platform,
                post(base + "/wechat-merchant-binding/verifications"),
                verifyOperationUid,
                Map.of(
                        "expectedMiniappVersion", 2,
                        "note", "相同键但不同请求"),
                409);
        assertEquals(
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                json(changedVerifyReplay).path("code").asText());
        assertObjectAuditSummary(
                verifyOperationUid,
                "wechat-merchant-binding.verify",
                "VERIFIED");

        Map<String, Object> immutableVerificationEvidence =
                merchantBindingVerificationEvidence(
                        tenantCode, organizationCode);
        Thread.sleep(10L);

        UUID reverifyOperationUid = UUID.randomUUID();
        Map<String, Object> reverifyRequest = Map.of(
                "expectedMiniappVersion", 2,
                "expectedBindingVersion", 0,
                "note", "再次核对外部绑定事实");
        JsonNode reverified = data(write(
                platform,
                post(base + "/wechat-merchant-binding/verifications"),
                reverifyOperationUid,
                reverifyRequest,
                200));
        assertEquals("VERIFIED", reverified.path("status").asText());
        assertEquals(1, reverified.path("bindingVersion").asLong());
        assertEquals(immutableVerificationEvidence,
                merchantBindingVerificationEvidence(
                        tenantCode, organizationCode));
        assertEquals(reverified, data(write(
                platform,
                post(base + "/wechat-merchant-binding/verifications"),
                reverifyOperationUid,
                reverifyRequest,
                200)));
        assertObjectAuditSummary(
                reverifyOperationUid,
                "wechat-merchant-binding.verify",
                "VERIFIED");

        UUID disableOperationUid = UUID.randomUUID();
        Map<String, Object> disableRequest = Map.of(
                "expectedBindingVersion", 1,
                "reason", "回归测试禁用");
        JsonNode disabled = data(write(
                platform,
                post(base + "/wechat-merchant-binding/disablements"),
                disableOperationUid,
                disableRequest,
                200));
        assertEquals("DISABLED", disabled.path("status").asText());
        assertEquals(2, disabled.path("bindingVersion").asLong());
        assertEquals(disabled, data(write(
                platform,
                post(base + "/wechat-merchant-binding/disablements"),
                disableOperationUid,
                disableRequest,
                200)));
        assertObjectAuditSummary(
                disableOperationUid,
                "wechat-merchant-binding.disable",
                "DISABLED");
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

    private void assertObjectAuditSummary(
            UUID operationUid,
            String actionCode,
            String bindingStatus) {
        Map<String, Object> auditRow = jdbc.queryForMap("""
                SELECT JSON_TYPE(safe_change_summary) AS summary_type,
                       action_code,
                       JSON_TYPE(JSON_EXTRACT(
                           safe_change_summary, '$.response')) AS response_type,
                       CHAR_LENGTH(JSON_UNQUOTE(JSON_EXTRACT(
                           safe_change_summary, '$.fingerprint'))) AS fingerprint_length,
                       JSON_UNQUOTE(JSON_EXTRACT(
                           safe_change_summary, '$.bindingStatus'))
                           AS binding_status
                FROM ops_audit_log
                WHERE succeeded_operation_uid = ?
                """, operationUid.toString());
        assertEquals("OBJECT", auditRow.get("summary_type"));
        assertEquals(actionCode, auditRow.get("action_code"));
        assertEquals("OBJECT", auditRow.get("response_type"));
        assertEquals(64L,
                ((Number) auditRow.get("fingerprint_length")).longValue());
        assertEquals(bindingStatus, auditRow.get("binding_status"));
    }

    private Map<String, Object> merchantBindingVerificationEvidence(
            String tenantCode,
            String organizationCode) {
        return jdbc.queryForMap("""
                SELECT b.organization_miniapp_id, b.appid,
                       b.miniapp_lock_version_snapshot,
                       b.merchant_profile_id,
                       b.verified_by_platform_admin_id,
                       b.verified_at, b.created_at
                FROM fund_miniapp_merchant_binding b
                JOIN iam_tenant t ON t.id = b.tenant_id
                JOIN iam_organization o
                  ON o.tenant_id = b.tenant_id
                 AND o.id = b.organization_id
                WHERE t.tenant_code = ?
                  AND o.organization_code = ?
                """, tenantCode, organizationCode);
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

    private WithdrawalCreationFixture seedWithdrawalCreationFixture(
            String tenantCode,
            String organizationCode) {
        long[] scope = jdbc.queryForObject("""
                        SELECT tenant.id, organization.id,
                               merchant.id, admin.id
                        FROM iam_tenant tenant
                        JOIN iam_organization organization
                          ON organization.tenant_id = tenant.id
                        JOIN fund_wechat_merchant_profile merchant
                          ON merchant.status = 'ENABLED'
                        JOIN fund_payout_gate gate
                          ON gate.merchant_profile_id = merchant.id
                        JOIN iam_platform_admin admin
                          ON admin.login_name = ?
                        WHERE tenant.tenant_code = ?
                          AND organization.organization_code = ?
                        """,
                (rs, ignored) -> new long[]{
                        rs.getLong(1), rs.getLong(2),
                        rs.getLong(3), rs.getLong(4)},
                platformLogin, tenantCode, organizationCode);
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        String appid = "wxlock" + run;
        jdbc.update("""
                INSERT INTO iam_organization_miniapp (
                    tenant_id, organization_id, appid, display_name,
                    login_enabled, app_secret, activated_at, lock_version,
                    configured_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'Lock order miniapp', 1, ?, ?, 0,
                          ?, ?, ?)
                """, scope[0], scope[1], appid,
                "test-app-secret-" + run, now, now, now, now);
        long miniappId = jdbc.queryForObject(
                "SELECT id FROM iam_organization_miniapp WHERE appid = ?",
                Long.class, appid);
        UUID userUid = UUID.randomUUID();
        String openid = "lock-openid-" + run;
        String phone = "+8613" + String.format(
                "%09d", Math.floorMod(run.hashCode(), 1_000_000_000));
        jdbc.update("""
                INSERT INTO iam_organization_user (
                    organization_user_uid, tenant_id, organization_id,
                    organization_miniapp_id, openid, phone_e164,
                    phone_bound_at, status, auth_version, lock_version,
                    registered_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 0, 0, ?, ?, ?)
                """, userUid.toString(), scope[0], scope[1], miniappId,
                openid, phone, now, now, now, now);
        long userId = jdbc.queryForObject("""
                SELECT id FROM iam_organization_user
                WHERE organization_miniapp_id = ? AND openid = ?
                """, Long.class, miniappId, openid);
        jdbc.update("""
                INSERT INTO fund_user_wallet (
                    wallet_uid, tenant_id, organization_id,
                    organization_user_id, available_balance_cent,
                    frozen_withdrawal_cent, last_entry_sequence_no,
                    delivery_gate_state, lock_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1000, 0, 0, 'OPEN', 0, ?, ?)
                """, UUID.randomUUID().toString(), scope[0], scope[1],
                userId, now, now);
        jdbc.update("""
                INSERT INTO fund_organization_wallet_entry_counter (
                    organization_id, tenant_id,
                    last_visibility_sequence_no, lock_version, updated_at
                ) VALUES (?, ?, 0, 0, ?)
                """, scope[1], scope[0], now);
        jdbc.update("""
                INSERT INTO fund_miniapp_merchant_binding (
                    binding_uid, tenant_id, organization_id,
                    organization_miniapp_id, appid,
                    miniapp_lock_version_snapshot, merchant_profile_id,
                    status, verified_by_platform_admin_id, verified_at,
                    disabled_at, lock_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, 'VERIFIED', ?, ?, NULL,
                          0, ?, ?)
                """, UUID.randomUUID().toString(), scope[0], scope[1],
                miniappId, appid, scope[2], scope[3], now, now, now);
        long bindingId = jdbc.queryForObject("""
                SELECT id FROM fund_miniapp_merchant_binding
                WHERE organization_miniapp_id = ?
                """, Long.class, miniappId);
        jdbc.update("""
                UPDATE fund_organization_payout_account
                SET available_payout_cent = 1000,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE tenant_id = ? AND organization_id = ?
                """, now, scope[0], scope[1]);
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM fund_organization_wallet_entry_counter
                WHERE tenant_id = ? AND organization_id = ?
                """, Integer.class, scope[0], scope[1]));
        return new WithdrawalCreationFixture(
                scope[0], scope[1], miniappId, appid,
                userId, userUid, bindingId);
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
                    var registration =
                            new ReliableFundsTaskRegistrationPort
                                    .ReliableFundsTaskRegistration(
                                    scope[0], scope[1],
                                    "CREATE_NATIVE_PAYMENT",
                                    "CREATE_NATIVE_PAYMENT:" + rechargeNo,
                                    "RECHARGE_ORDER", rechargeNo, 1, "{}",
                                    RechargeApplicationService.sha256("{}"),
                                    20, null);
                    UUID firstTaskUid = reliableFundsTasks.register(
                            registration);
                    assertEquals(firstTaskUid, reliableFundsTasks.register(
                            registration));
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
            WithdrawalTaskFixture fixture = jdbc.queryForObject("""
                            SELECT tenant.id tenant_id,
                                   organization.id organization_id,
                                   account.id account_id,
                                   config.id config_id,
                                   config.version_no,
                                   config.hard_limit_cent,
                                   config.manual_min_cent,
                                   config.manual_max_cent,
                                   merchant.id merchant_id,
                                   merchant.mchid,
                                   merchant.scene_id,
                                   merchant.report_type,
                                   merchant.report_content,
                                   merchant.transfer_page_style,
                                   admin.id platform_admin_id
                            FROM iam_tenant tenant
                            JOIN iam_organization organization
                              ON organization.tenant_id = tenant.id
                            JOIN fund_organization_payout_account account
                              ON account.tenant_id = tenant.id
                             AND account.organization_id = organization.id
                            JOIN fund_organization_withdraw_config_head head
                              ON head.tenant_id = tenant.id
                             AND head.organization_id = organization.id
                            JOIN fund_organization_withdraw_config config
                              ON config.id = head.current_config_id
                            JOIN fund_wechat_merchant_profile merchant
                              ON merchant.status = 'ENABLED'
                            JOIN fund_payout_gate gate
                              ON gate.merchant_profile_id = merchant.id
                            JOIN iam_platform_admin admin
                              ON admin.login_name = ?
                            WHERE tenant.tenant_code = ?
                              AND organization.organization_code = ?
                            """,
                    (rs, ignored) -> new WithdrawalTaskFixture(
                            rs.getLong("tenant_id"),
                            rs.getLong("organization_id"),
                            rs.getLong("account_id"),
                            rs.getLong("config_id"),
                            rs.getLong("version_no"),
                            rs.getLong("hard_limit_cent"),
                            rs.getLong("manual_min_cent"),
                            rs.getLong("manual_max_cent"),
                            rs.getLong("merchant_id"),
                            rs.getString("mchid"),
                            rs.getString("scene_id"),
                            rs.getString("report_type"),
                            rs.getString("report_content"),
                            rs.getString("transfer_page_style"),
                            rs.getLong("platform_admin_id")),
                    platformLogin, tenantCode, organizationCode);
            LocalDateTime now = jdbc.queryForObject(
                    "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
            String appid = "wxwake" + run;
            jdbc.update("""
                    INSERT INTO iam_organization_miniapp (
                        tenant_id, organization_id, appid, display_name,
                        login_enabled, app_secret, activated_at, lock_version,
                        configured_at, created_at, updated_at
                    ) VALUES (?, ?, ?, 'Payout wake test', 1, ?, ?, 0,
                              ?, ?, ?)
                    """, fixture.tenantId(), fixture.organizationId(), appid,
                    "test-app-secret-" + run, now, now, now, now);
            long miniappId = jdbc.queryForObject(
                    "SELECT id FROM iam_organization_miniapp WHERE appid = ?",
                    Long.class, appid);
            String openid = "wake-openid-" + run;
            jdbc.update("""
                    INSERT INTO iam_organization_user (
                        organization_user_uid, tenant_id, organization_id,
                        organization_miniapp_id, openid, phone_e164,
                        phone_bound_at, status, auth_version, lock_version,
                        registered_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, NULL, 'ACTIVE', 0, 0,
                              ?, ?, ?)
                    """, UUID.randomUUID().toString(), fixture.tenantId(),
                    fixture.organizationId(), miniappId, openid,
                    now, now, now);
            long userId = jdbc.queryForObject("""
                    SELECT id FROM iam_organization_user
                    WHERE organization_miniapp_id = ? AND openid = ?
                    """, Long.class, miniappId, openid);
            jdbc.update("""
                    INSERT INTO fund_user_wallet (
                        wallet_uid, tenant_id, organization_id,
                        organization_user_id, available_balance_cent,
                        frozen_withdrawal_cent, last_entry_sequence_no,
                        delivery_gate_state, lock_version, created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, 1000, 0, 0, 'OPEN', 0, ?, ?)
                    """, UUID.randomUUID().toString(), fixture.tenantId(),
                    fixture.organizationId(), userId, now, now);
            long walletId = jdbc.queryForObject("""
                    SELECT id FROM fund_user_wallet
                    WHERE tenant_id = ? AND organization_id = ?
                      AND organization_user_id = ?
                    """, Long.class, fixture.tenantId(),
                    fixture.organizationId(), userId);
            jdbc.update("""
                    INSERT INTO fund_miniapp_merchant_binding (
                        binding_uid, tenant_id, organization_id,
                        organization_miniapp_id, appid,
                        miniapp_lock_version_snapshot, merchant_profile_id,
                        status, verified_by_platform_admin_id, verified_at,
                        disabled_at, lock_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, 'VERIFIED', ?, ?, NULL,
                              0, ?, ?)
                    """, UUID.randomUUID().toString(), fixture.tenantId(),
                    fixture.organizationId(), miniappId, appid,
                    fixture.merchantId(), fixture.platformAdminId(),
                    now, now, now);
            long bindingId = jdbc.queryForObject("""
                    SELECT id FROM fund_miniapp_merchant_binding
                    WHERE organization_miniapp_id = ?
                    """, Long.class, miniappId);
            String withdrawalNo = "WDWAKE" + run;
            jdbc.update("""
                    INSERT INTO fund_withdrawal_order (
                        withdrawal_order_no, tenant_id, organization_id,
                        organization_user_id, wallet_id,
                        organization_payout_account_id, withdraw_config_id,
                        withdraw_config_version_no, hard_limit_cent_snapshot,
                        manual_min_cent_snapshot, manual_max_cent_snapshot,
                        manual_review_free_threshold_cent_snapshot,
                        amount_cent, miniapp_merchant_binding_id,
                        organization_miniapp_id, merchant_profile_id,
                        mchid_snapshot, appid_snapshot, openid_snapshot,
                        business_state, negative_balance_pause,
                        post_boundary_risk, pre_channel_block_reason,
                        channel_boundary_at, long_unsettled_at, reviewed_at,
                        channel_terminal_at, ended_at, lock_version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 10, ?, ?,
                              ?, ?, ?, ?, 'READY_TO_SUBMIT', 0, 0, NULL,
                              NULL, NULL, ?, NULL, NULL, 0, ?, ?)
                    """, withdrawalNo, fixture.tenantId(),
                    fixture.organizationId(), userId, walletId,
                    fixture.accountId(), fixture.configId(),
                    fixture.configVersion(), fixture.hardLimitCent(),
                    fixture.minimumCent(), fixture.maximumCent(), bindingId,
                    miniappId, fixture.merchantId(), fixture.mchid(), appid,
                    openid, now, now, now);
            String matchingKey = "SUBMIT_MERCHANT_TRANSFER:"
                    + withdrawalNo + ":MATCH";
            String otherKey = "SUBMIT_MERCHANT_TRANSFER:"
                    + withdrawalNo + ":OTHER";
            String blockedKey = "SUBMIT_MERCHANT_TRANSFER:"
                    + withdrawalNo + ":BLOCKED";
            for (String key : new String[]{matchingKey, otherKey, blockedKey}) {
                reliableFundsTasks.register(
                        new ReliableFundsTaskRegistrationPort
                                .ReliableFundsTaskRegistration(
                                fixture.tenantId(), fixture.organizationId(),
                                "SUBMIT_MERCHANT_TRANSFER", key,
                                "WITHDRAWAL_ORDER", withdrawalNo, 1, "{}",
                                RechargeApplicationService.sha256("{}"),
                                20, null));
            }
            LocalDateTime taskNow = jdbc.queryForObject(
                    "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
            UUID pausedEventUid = UUID.randomUUID();
            UUID otherPausedEventUid = UUID.randomUUID();
            UUID matchingTaskUid = UUID.fromString(jdbc.queryForObject("""
                    SELECT task_uid FROM ops_reliable_task WHERE task_key = ?
                    """, String.class, matchingKey.toUpperCase(Locale.ROOT)));
            UUID otherTaskUid = UUID.fromString(jdbc.queryForObject("""
                    SELECT task_uid FROM ops_reliable_task WHERE task_key = ?
                    """, String.class, otherKey.toUpperCase(Locale.ROOT)));
            long matchingTaskId = jdbc.queryForObject("""
                    SELECT id FROM ops_reliable_task WHERE task_uid = ?
                    """, Long.class, matchingTaskUid.toString());
            long withdrawalId = jdbc.queryForObject("""
                    SELECT id FROM fund_withdrawal_order
                    WHERE withdrawal_order_no = ?
                    """, Long.class, withdrawalNo);
            String outBillNo = "MTWAKE" + run;
            byte[] requestHash = RechargeApplicationService.sha256(
                    "payout-wake-request|" + withdrawalNo);
            jdbc.update("""
                    INSERT INTO fund_wechat_transfer (
                        transfer_uid, tenant_id, organization_id,
                        withdrawal_order_id, merchant_profile_id,
                        miniapp_merchant_binding_id,
                        organization_miniapp_id, out_bill_no,
                        transfer_bill_no, amount_cent, mchid_snapshot,
                        appid_snapshot, openid_snapshot, scene_id_snapshot,
                        report_type_snapshot, report_content_snapshot,
                        transfer_remark, transfer_page_style_snapshot,
                        notify_url_sha256, request_sha256, channel_state,
                        terminal_classification, package_info,
                        last_api_error_code, terminal_fail_reason,
                        state_conflict, submitted_at, channel_updated_at,
                        terminal_at, lock_version, created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, NULL, 10, ?, ?, ?, ?, ?, ?,
                        '环保回收提现', ?, ?, ?, 'NOT_ENOUGH', 'NON_TERMINAL',
                        NULL, 'NOT_ENOUGH', NULL, 0, ?, ?, NULL, 0, ?, ?
                    )
                    """, UUID.randomUUID().toString(), fixture.tenantId(),
                    fixture.organizationId(), withdrawalId,
                    fixture.merchantId(), bindingId, miniappId, outBillNo,
                    fixture.mchid(), appid, openid, fixture.sceneId(),
                    fixture.reportType(), fixture.reportContent(),
                    fixture.pageStyle(), requestHash, requestHash,
                    now, now, now, now);
            long transferId = jdbc.queryForObject("""
                    SELECT id FROM fund_wechat_transfer
                    WHERE out_bill_no = ?
                    """, Long.class, outBillNo);
            UUID attemptUid = UUID.randomUUID();
            UUID leaseToken = UUID.randomUUID();
            jdbc.update("""
                    INSERT INTO ops_task_attempt (
                        attempt_uid, task_id, scope_kind, tenant_id,
                        organization_id, attempt_no, lease_token,
                        claimed_wake_version, worker_id, claimed_at,
                        lease_until, external_call_may_have_started_at,
                        reclaimed_at, result_recorded_at, action_kind,
                        technical_result, request_sha256, response_sha256,
                        http_status, external_api_error_code, duration_ms,
                        redacted_diagnostic, created_at
                    ) VALUES (
                        ?, ?, 'ORGANIZATION', ?, ?, 1, ?, 0,
                        'payout-wake-test', ?, ?, ?, NULL, ?, 'SUBMIT',
                        'TECHNICAL_SUCCESS', ?, ?, 200, 'NOT_ENOUGH', 1,
                        'synthetic NOT_ENOUGH evidence', ?
                    )
                    """, attemptUid.toString(), matchingTaskId,
                    fixture.tenantId(), fixture.organizationId(),
                    leaseToken.toString(), now, now.plusMinutes(5), now, now,
                    requestHash, requestHash, now);
            long attemptId = jdbc.queryForObject("""
                    SELECT id FROM ops_task_attempt WHERE attempt_uid = ?
                    """, Long.class, attemptUid.toString());
            UUID observationUid = UUID.randomUUID();
            jdbc.update("""
                    INSERT INTO fund_wechat_transfer_observation (
                        observation_uid, tenant_id, organization_id,
                        transfer_id, observation_type, evidence_source_kind,
                        source_scope_kind, source_inbox_id,
                        source_task_attempt_id, raw_channel_state,
                        api_error_code, terminal_fail_reason,
                        observed_mchid, observed_appid, out_bill_no,
                        observed_out_bill_no, transfer_bill_no, package_info,
                        amount_cent, openid, channel_occurred_at,
                        content_sha256, observed_at, created_at
                    ) VALUES (
                        ?, ?, ?, ?, 'SUBMIT_RESPONSE', 'TASK_ATTEMPT',
                        'ORGANIZATION', NULL, ?, 'NOT_ENOUGH', 'NOT_ENOUGH',
                        NULL, ?, ?, ?, ?, NULL, NULL, 10, ?, ?, ?, ?, ?
                    )
                    """, observationUid.toString(), fixture.tenantId(),
                    fixture.organizationId(), transferId, attemptId,
                    fixture.mchid(), appid, outBillNo, outBillNo, openid,
                    now, requestHash, now, now);
            long observationId = jdbc.queryForObject("""
                    SELECT id FROM fund_wechat_transfer_observation
                    WHERE observation_uid = ?
                    """, Long.class, observationUid.toString());
            jdbc.update("""
                    INSERT INTO fund_payout_gate_event (
                        event_uid, merchant_profile_id, event_type,
                        triggering_transfer_observation_id,
                        triggering_transfer_id, original_pause_event_id,
                        original_pause_event_type,
                        restored_by_platform_admin_id,
                        restore_request_sha256, gate_version_before,
                        gate_version_after, note, occurred_at, created_at
                    ) VALUES (
                        ?, ?, 'PAUSED', ?, ?, NULL, NULL, NULL,
                        NULL, NULL, NULL, 'synthetic NOT_ENOUGH pause', ?, ?
                    )
                    """, pausedEventUid.toString(), fixture.merchantId(),
                    observationId, transferId, now, now);
            long pauseEventId = jdbc.queryForObject("""
                    SELECT id FROM fund_payout_gate_event WHERE event_uid = ?
                    """, Long.class, pausedEventUid.toString());
            assertEquals(1, jdbc.update("""
                    UPDATE fund_payout_gate
                    SET gate_state = 'PAUSED_NOT_ENOUGH',
                        current_pause_event_id = ?, paused_at = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE merchant_profile_id = ? AND gate_state = 'OPEN'
                    """, pauseEventId, now, now, fixture.merchantId()));
            fundsOperationalControl.markPayoutTaskWaiting(
                    matchingTaskUid, fixture.merchantId(), pausedEventUid,
                    taskNow);
            fundsOperationalControl.markPayoutTaskWaiting(
                    otherTaskUid, fixture.merchantId(), otherPausedEventUid,
                    taskNow);
            jdbc.update("""
                    UPDATE ops_reliable_task
                    SET state = 'BLOCKED', next_run_at = NULL,
                        completed_at = ?, blocked_reason_code = 'DATA_ERROR',
                        blocked_diagnostic = 'unrelated blocked task',
                        handled_wake_version = wake_version,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE task_key = ?
                    """, taskNow, taskNow,
                    blockedKey.toUpperCase(Locale.ROOT));
            fundsOperationalControl.observePayoutLiquidityPause(
                    fixture.merchantId(), pausedEventUid, taskNow);
            assertEquals("OPEN|1", jdbc.queryForObject("""
                            SELECT CONCAT(status, '|', discovery_count)
                            FROM ops_alert
                            WHERE source_type = 'PAYOUT_GATE_PAUSE'
                              AND source_key = ?
                            """, String.class,
                    "PAYOUT_GATE:" + fixture.merchantId() + ":"
                            + pausedEventUid));
            long pausedGateVersion = jdbc.queryForObject("""
                    SELECT lock_version FROM fund_payout_gate
                    WHERE merchant_profile_id = ?
                    """, Long.class, fixture.merchantId());
            FundsIdentityAccessPort identity = mock(
                    FundsIdentityAccessPort.class);
            when(identity.authorizePlatform()).thenReturn(
                    new AuthorizedPlatformIdentity(
                            fixture.platformAdminId(), UUID.randomUUID(),
                            UUID.randomUUID(), "payout restore test"));
            AuditPort audit = mock(AuditPort.class);
            when(audit.append(any())).thenReturn(1L);
            WithdrawalApplicationService service =
                    new WithdrawalApplicationService(
                            jdbc, new FundsAccessService(jdbc, identity),
                            reliableFundsTasks,
                            mock(ReliableFundsAttemptBoundaryPort.class),
                            mock(MerchantTransferChannelPort.class),
                            new TransactionTemplate(transactionManager), audit,
                            fundsOperationalControl, fundsListCursorCodec,
                            "https://fake.invalid");
            UUID restoreOperationUid = UUID.randomUUID();
            RestorePayoutGateRequest restoreRequest =
                    new RestorePayoutGateRequest(
                            pausedGateVersion, pausedEventUid, true,
                            "公司运营账户已经补资");
            PayoutGateView restored = service.restorePayoutGate(
                    restoreOperationUid, restoreRequest);
            assertEquals("OPEN", restored.status());
            assertEquals(pausedGateVersion + 1, restored.version());
            PayoutGateView replayed = service.restorePayoutGate(
                    restoreOperationUid, restoreRequest);
            assertEquals(restored, replayed);
            TargetApiException conflict = assertThrows(
                    TargetApiException.class,
                    () -> service.restorePayoutGate(
                            restoreOperationUid,
                            new RestorePayoutGateRequest(
                                    pausedGateVersion, pausedEventUid, true,
                                    "同一个键但请求内容不同")));
            assertEquals("COMMON.IDEMPOTENCY_KEY_CONFLICT", conflict.code());
            assertEquals(1, jdbc.queryForObject("""
                    SELECT COUNT(*) FROM fund_payout_gate_event
                    WHERE event_uid = ? AND event_type = 'RESTORED'
                    """, Integer.class, restoreOperationUid.toString()));
            assertEquals("PENDING|1|NULL", jdbc.queryForObject("""
                    SELECT CONCAT(state, '|', wake_version, '|',
                                  COALESCE(dispatch_wait_reason, 'NULL'))
                    FROM ops_reliable_task WHERE task_key = ?
                    """, String.class,
                    matchingKey.toUpperCase(Locale.ROOT)));
            assertEquals("PENDING|0|PAYOUT_NOT_ENOUGH:"
                            + otherPausedEventUid,
                    jdbc.queryForObject("""
                            SELECT CONCAT(state, '|', wake_version, '|',
                                          dispatch_wait_reason)
                            FROM ops_reliable_task WHERE task_key = ?
                            """, String.class,
                            otherKey.toUpperCase(Locale.ROOT)));
            assertEquals("BLOCKED|0|DATA_ERROR", jdbc.queryForObject("""
                    SELECT CONCAT(state, '|', wake_version, '|',
                                  blocked_reason_code)
                    FROM ops_reliable_task WHERE task_key = ?
                    """, String.class,
                    blockedKey.toUpperCase(Locale.ROOT)));
            assertEquals("RESOLVED", jdbc.queryForObject("""
                            SELECT status FROM ops_alert
                            WHERE source_type = 'PAYOUT_GATE_PAUSE'
                              AND source_key = ?
                            """, String.class,
                    "PAYOUT_GATE:" + fixture.merchantId() + ":"
                            + pausedEventUid));
            status.setRollbackOnly();
        });
    }

    private record WithdrawalTaskFixture(
            long tenantId,
            long organizationId,
            long accountId,
            long configId,
            long configVersion,
            long hardLimitCent,
            long minimumCent,
            long maximumCent,
            long merchantId,
            String mchid,
            String sceneId,
            String reportType,
            String reportContent,
            String pageStyle,
            long platformAdminId) {
    }

    private record WithdrawalCreationFixture(
            long tenantId,
            long organizationId,
            long miniappId,
            String appid,
            long userId,
            UUID userUid,
            long bindingId) {
    }

    private record FundsTaskRef(long taskId, UUID taskUid) {
    }

    private record PaymentNotificationFixture(
            String mchid,
            String appid,
            String outTradeNo,
            long amountCent) {
    }

    private static final class ScriptedNativePaymentChannel
            implements NativePaymentChannelPort {

        private int createCount;
        private int queryCount;
        private NativePaymentRequest request;

        @Override
        public NativePaymentResult create(NativePaymentRequest request) {
            createCount++;
            this.request = request;
            return new NativePaymentResult(
                    NativePaymentResult.Outcome.ACCEPTED,
                    "NOTPAY", "weixin://wxpay/bizpayurl?pr=FAKETEST",
                    null, null, null, Instant.now());
        }

        @Override
        public NativePaymentResult query(NativePaymentQuery query) {
            queryCount++;
            assertEquals(request.mchid(), query.mchid());
            assertEquals(request.outTradeNo(), query.outTradeNo());
            long observedAmount = queryCount == 1
                    ? request.amountCent() + 1 : request.amountCent();
            return new NativePaymentResult(
                    NativePaymentResult.Outcome.SUCCEEDED,
                    "SUCCESS", null, "WXNATIVE" + request.outTradeNo(),
                    null, "paid", Instant.now(), request.mchid(),
                    request.appid(), request.outTradeNo(), observedAmount,
                    "CNY");
        }

        @Override
        public NativePaymentResult close(NativePaymentQuery query) {
            throw new UnsupportedOperationException(
                    "close is outside this evidence scenario");
        }
    }

    private static final class DuplicateRefundNativeChannel
            implements NativePaymentChannelPort {

        private int createCount;
        private int queryCount;
        private NativePaymentRequest request;

        @Override
        public NativePaymentResult create(NativePaymentRequest request) {
            createCount++;
            this.request = request;
            return new NativePaymentResult(
                    NativePaymentResult.Outcome.ORDER_ALREADY_EXISTS,
                    "API_ERROR", null, null, "OUT_TRADE_NO_USED",
                    "order already exists", Instant.now());
        }

        @Override
        public NativePaymentResult query(NativePaymentQuery query) {
            queryCount++;
            assertEquals(request.mchid(), query.mchid());
            assertEquals(request.outTradeNo(), query.outTradeNo());
            return new NativePaymentResult(
                    NativePaymentResult.Outcome.REFUNDED,
                    "REFUND", null, "WXREFUND" + request.outTradeNo(),
                    null, "refunded", Instant.now(),
                    request.mchid(), request.appid(), request.outTradeNo(),
                    request.amountCent(), "CNY");
        }

        @Override
        public NativePaymentResult close(NativePaymentQuery query) {
            throw new UnsupportedOperationException();
        }
    }

    private static final class ClosingNativeChannel
            implements NativePaymentChannelPort {

        private NativePaymentRequest request;
        private int createCount;
        private int closeCount;

        @Override
        public NativePaymentResult create(NativePaymentRequest request) {
            createCount++;
            this.request = request;
            if (createCount == 1) {
                return new NativePaymentResult(
                        NativePaymentResult.Outcome.RETRYABLE_FAILURE,
                        "API_ERROR", null, null, "SYSTEM_ERROR",
                        "temporary create failure", Instant.now());
            }
            return new NativePaymentResult(
                    NativePaymentResult.Outcome.ACCEPTED,
                    "NOTPAY", "weixin://wxpay/bizpayurl?pr=CLOSETEST",
                    null, null, null, Instant.now());
        }

        @Override
        public NativePaymentResult query(NativePaymentQuery query) {
            return new NativePaymentResult(
                    NativePaymentResult.Outcome.ACCEPTED,
                    "NOTPAY", null, null, null, "not paid", Instant.now(),
                    request.mchid(), request.appid(), request.outTradeNo(),
                    request.amountCent(), "CNY");
        }

        @Override
        public NativePaymentResult close(NativePaymentQuery query) {
            closeCount++;
            if (closeCount == 1) {
                return new NativePaymentResult(
                        NativePaymentResult.Outcome.RETRYABLE_FAILURE,
                        "API_ERROR", null, null, "SYSTEM_ERROR",
                        "temporary close failure", Instant.now());
            }
            return new NativePaymentResult(
                    NativePaymentResult.Outcome.CLOSED,
                    "CLOSED", null, null, null, null, Instant.now());
        }
    }

    private static final class FailingMerchantTransferChannel
            implements MerchantTransferChannelPort {

        private MerchantTransferRequest request;

        @Override
        public MerchantTransferResult submit(MerchantTransferRequest request) {
            this.request = request;
            return new MerchantTransferResult(
                    MerchantTransferResult.Outcome.WAIT_USER_CONFIRM,
                    "WAIT_USER_CONFIRM", transferBillNo(), "package-info",
                    null, null, null,
                    Instant.now(), request.mchid(), request.outBillNo(),
                    request.appid(), request.amountCent(), request.openid());
        }

        @Override
        public MerchantTransferResult query(MerchantTransferQuery query) {
            assertEquals(request.mchid(), query.mchid());
            assertEquals(request.outBillNo(), query.outBillNo());
            return new MerchantTransferResult(
                    MerchantTransferResult.Outcome.FAIL,
                    "FAIL", transferBillNo(), null, null,
                    "RECIPIENT_ACCOUNT_ABNORMAL", "synthetic terminal fail",
                    Instant.now(), request.mchid(), request.outBillNo(),
                    request.appid(), request.amountCent(), request.openid());
        }

        private String transferBillNo() {
            return "WXFAIL" + request.outBillNo();
        }
    }

    private static final class ScriptedMerchantTransferChannel
            implements MerchantTransferChannelPort {

        private int submitCount;
        private int queryCount;
        private MerchantTransferRequest originalRequest;

        @Override
        public MerchantTransferResult submit(MerchantTransferRequest request) {
            submitCount++;
            if (submitCount == 1) {
                originalRequest = request;
                return new MerchantTransferResult(
                        MerchantTransferResult.Outcome.RETRYABLE_FAILURE,
                        "API_ERROR", null, null, "SYSTEM_ERROR", null,
                        "uncertain submit result", Instant.now());
            }
            assertEquals(originalRequest, request,
                    "confirmed missing bill must reuse every original parameter");
            return new MerchantTransferResult(
                    MerchantTransferResult.Outcome.WAIT_USER_CONFIRM,
                    "WAIT_USER_CONFIRM", transferBillNo(),
                    "package-info", null, null, null, Instant.now());
        }

        @Override
        public MerchantTransferResult query(MerchantTransferQuery query) {
            queryCount++;
            assertEquals(originalRequest.mchid(), query.mchid());
            assertEquals(originalRequest.outBillNo(), query.outBillNo());
            if (queryCount == 1) {
                return new MerchantTransferResult(
                        MerchantTransferResult.Outcome.NOT_FOUND,
                        "NOT_FOUND", null, null, "NOT_FOUND", null,
                        "original bill is absent", Instant.now());
            }
            long observedAmount = queryCount == 2
                    ? originalRequest.amountCent() + 1
                    : originalRequest.amountCent();
            return new MerchantTransferResult(
                    MerchantTransferResult.Outcome.SUCCESS,
                    "SUCCESS", transferBillNo(), null, null, null,
                    null, Instant.now(), originalRequest.mchid(),
                    originalRequest.outBillNo(), originalRequest.appid(),
                    observedAmount, originalRequest.openid());
        }

        private String transferBillNo() {
            return "WXTR" + originalRequest.outBillNo();
        }
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
