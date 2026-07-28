package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.identity.api.command.OrganizationUserRegistrationCommand;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationParticipant;
import org.enveloping.ecobin.identity.application.directory.TargetOrganizationUserBindingService;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.application.web.WebAccountType;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.AccountVersionCommand;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.BindingSnapshot;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.OrganizationUserView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.PageData;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.SetStaffMiniappBindingRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.StaffMiniappBindingView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.VersionCommand;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

@SpringBootTest(properties = {
        "spring.datasource.url=${ECOBIN_V02_MYSQL_URL}",
        "spring.datasource.username=${ECOBIN_V02_MYSQL_USERNAME}",
        "spring.datasource.password=${ECOBIN_V02_MYSQL_PASSWORD}",
        "spring.sql.init.mode=never",
        "jwt.secret=v02_mysql_test_jwt_secret_at_least_32_bytes_long",
        "app.crypto.aes-key=v02_mysql_test_aes_key",
        "ecobin.database.epoch.test-bypass=false",
        "ecobin.external.mode=fake",
        "ecobin.external.fake.block-inbound=true",
        "onenet.subscription.enabled=false"
})
@AutoConfigureMockMvc
@Import(TargetMiniappV02MysqlIntegrationTest.ProbeConfiguration.class)
@EnabledIfEnvironmentVariable(
        named = "ECOBIN_V02_MYSQL_URL",
        matches = "jdbc:mysql:.+")
class TargetMiniappV02MysqlIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JdbcTemplate jdbc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private TargetOrganizationUserBindingService bindingService;

    @Autowired
    private ProbeParticipant participant;

    private String run;
    private String tenantCode;
    private String organizationCode;
    private String appId;
    private String deploymentCode;
    private long tenantId;
    private long organizationId;
    private long miniappId;
    private long platformAdminId;
    private UUID platformAdminUid;
    private UUID staffUid;

    @BeforeEach
    void seedScope() {
        run = Long.toUnsignedString(System.nanoTime(), 36);
        tenantCode = code("t");
        organizationCode = code("o");
        appId = "wx" + UUID.randomUUID().toString()
                .replace("-", "").substring(0, 16);
        deploymentCode = "Dp_" + run;
        participant.reset();

        platformAdminUid = UUID.randomUUID();
        jdbc.update("""
                        INSERT INTO iam_platform_admin (
                            platform_admin_uid, login_name, password_hash,
                            display_name, enabled, failed_login_count,
                            locked_until, auth_version, password_changed_at,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, 'unused-test-hash', 'V02 operator', 1, 0,
                            NULL, 0, UTC_TIMESTAMP(3), 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                platformAdminUid.toString(),
                "v02-platform-" + run);
        platformAdminId = jdbc.queryForObject("""
                        SELECT id FROM iam_platform_admin
                        WHERE platform_admin_uid = ?
                        """, Long.class, platformAdminUid.toString());

        jdbc.update("""
                        INSERT INTO iam_tenant (
                            tenant_code, enterprise_name, status,
                            contact_name, contact_phone, contact_address,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, 'V02 tenant', 'ENABLED',
                            NULL, NULL, NULL, 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """, tenantCode);
        tenantId = jdbc.queryForObject("""
                        SELECT id FROM iam_tenant WHERE tenant_code = ?
                        """, Long.class, tenantCode);
        jdbc.update("""
                        INSERT INTO iam_organization (
                            tenant_id, organization_code,
                            organization_name, status,
                            contact_phone, contact_address,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, 'V02 organization', 'ENABLED',
                            NULL, NULL, 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """, tenantId, organizationCode);
        organizationId = jdbc.queryForObject("""
                        SELECT id FROM iam_organization
                        WHERE tenant_id = ? AND organization_code = ?
                        """, Long.class, tenantId, organizationCode);
        jdbc.update("""
                        INSERT INTO iam_organization_miniapp (
                            tenant_id, organization_id, appid,
                            display_name, login_enabled, secret_ref,
                            activated_at, lock_version,
                            configured_at, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 'V02 miniapp', 1, 'fake:credential',
                            UTC_TIMESTAMP(3), 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3),
                            UTC_TIMESTAMP(3)
                        )
                        """, tenantId, organizationId, appId);
        miniappId = jdbc.queryForObject("""
                        SELECT id FROM iam_organization_miniapp
                        WHERE appid = ?
                        """, Long.class, appId);

        staffUid = UUID.randomUUID();
        jdbc.update("""
                        INSERT INTO iam_staff_account (
                            tenant_id, staff_account_uid, account_kind,
                            login_name, password_hash, display_name,
                            contact_phone, enabled, failed_login_count,
                            locked_until, auth_version, password_changed_at,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, 'TENANT_PRINCIPAL', ?,
                            'unused-test-hash', 'V02 principal',
                            NULL, 1, 0, NULL, 0, UTC_TIMESTAMP(3),
                            0, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                tenantId,
                staffUid.toString(),
                "v02-principal-" + run);

        jdbc.update("""
                        INSERT INTO dev_device_asset (
                            hardware_sn, model_name, production_batch,
                            expected_port_count, lifecycle_status,
                            retired_at, retirement_reason, lock_version,
                            created_at, updated_at
                        ) VALUES (
                            ?, 'V02 model', NULL, 1, 'ALLOCATED',
                            NULL, NULL, 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """, "V02-SN-" + run);
        long assetId = jdbc.queryForObject("""
                        SELECT id FROM dev_device_asset WHERE hardware_sn = ?
                        """, Long.class, "V02-SN-" + run);
        jdbc.update("""
                        INSERT INTO dev_device_deployment (
                            tenant_id, organization_id, asset_id,
                            public_code, lifecycle_status,
                            business_enabled, commissioned_at,
                            enabled_at, ended_at, end_method, end_reason,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, 'PENDING_INSTALL',
                            0, NULL, NULL, NULL, NULL, NULL,
                            0, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                tenantId, organizationId, assetId, deploymentCode);
        long deploymentId = jdbc.queryForObject("""
                        SELECT id FROM dev_device_deployment
                        WHERE public_code = ?
                        """, Long.class, deploymentCode);
        jdbc.update("""
                        INSERT INTO dev_asset_active_deployment (
                            asset_id, tenant_id, organization_id,
                            deployment_id, acquired_at
                        ) VALUES (?, ?, ?, ?, UTC_TIMESTAMP(3))
                        """,
                assetId, tenantId, organizationId, deploymentId);
    }

    @AfterEach
    void clearContexts() {
        TargetWebActorContext.clear();
    }

    @Test
    void registrationPhoneBindingAndStaffBindingFormOneAtomicIdentityFlow()
            throws Exception {
        String ordinaryCode = "fake:ordinary:" + run;
        JsonNode first = login(ordinaryCode, null, 201);
        assertEquals("miniapp", first.path("audience").asText());
        assertEquals("USER", first.path("entryMode").asText());
        assertFalse(first.path("phoneBound").asBoolean());
        assertTrue(first.path("isNewRegistration").asBoolean());
        String ordinaryToken = first.path("accessToken").asText();
        UUID organizationUserUid = UUID.fromString(
                first.path("subjectUid").asText());

        JsonNode repeat = login(ordinaryCode, null, 201);
        assertFalse(repeat.path("isNewRegistration").asBoolean());
        assertEquals(organizationUserUid.toString(),
                repeat.path("subjectUid").asText());
        assertUserAndWalletCounts(1, 1);

        UUID phoneOperation = UUID.randomUUID();
        JsonNode phone = phoneBind(
                ordinaryToken,
                phoneOperation,
                "fake-phone:+8613812345678",
                201);
        assertEquals("+86138****5678",
                phone.path("maskedPhoneNumber").asText());
        phoneBind(
                ordinaryToken,
                phoneOperation,
                "fake-phone:+8613812345678",
                200);

        String sourcedCode = "fake:sourced:" + run;
        JsonNode sourced = login(sourcedCode, deploymentCode, 201);
        String sourcedToken = sourced.path("accessToken").asText();
        UUID sourcedUid = UUID.fromString(
                sourced.path("subjectUid").asText());
        Long attributedDeployment = jdbc.queryForObject("""
                        SELECT registered_via_deployment_id
                        FROM iam_organization_user
                        WHERE organization_user_uid = ?
                        """, Long.class, sourcedUid.toString());
        assertNotNull(attributedDeployment);
        login(sourcedCode, null, 201);
        assertEquals(attributedDeployment, jdbc.queryForObject("""
                        SELECT registered_via_deployment_id
                        FROM iam_organization_user
                        WHERE organization_user_uid = ?
                        """, Long.class, sourcedUid.toString()));
        JsonNode duplicatePhone = phoneBind(
                sourcedToken,
                UUID.randomUUID(),
                "fake-phone:+8613812345678",
                409);
        assertEquals("IDENTITY.PHONE_ALREADY_USED",
                duplicatePhone.path("code").asText());

        asPlatformActor();
        var lookup = bindingService.lookupByPhone(
                tenantCode,
                organizationCode,
                "13812345678");
        assertEquals(organizationUserUid, lookup.organizationUserUid());
        assertEquals("+86138****5678", lookup.maskedPhoneNumber());
        StaffMiniappBindingView binding = bindingService.setBinding(
                UUID.randomUUID(),
                tenantCode,
                organizationCode,
                staffUid,
                new SetStaffMiniappBindingRequest(
                        organizationUserUid,
                        null,
                        null,
                        "manual V02 verification"));
        clearContexts();

        bearerGet(
                ordinaryToken,
                "/api/v1/miniapp/auth/sessions/current",
                401);
        JsonNode management = login(ordinaryCode, null, 201);
        assertEquals("miniapp-staff",
                management.path("audience").asText());
        assertEquals("MANAGEMENT",
                management.path("entryMode").asText());
        assertEquals(staffUid.toString(),
                management.path("subjectUid").asText());
        String managementToken =
                management.path("accessToken").asText();

        asPlatformActor();
        StaffMiniappBindingView revoked = bindingService.revokeBinding(
                UUID.randomUUID(),
                tenantCode,
                organizationCode,
                binding.bindingUid(),
                new VersionCommand(
                        binding.version(),
                        "V02 revocation verification"));
        clearContexts();
        assertEquals("REVOKED", revoked.status());
        bearerGet(
                managementToken,
                "/api/v1/miniapp-staff/auth/sessions/current",
                401);
        JsonNode ordinaryAgain = login(ordinaryCode, null, 201);
        assertEquals("miniapp",
                ordinaryAgain.path("audience").asText());
        assertEquals("USER",
                ordinaryAgain.path("entryMode").asText());
        assertTrue(ordinaryAgain.path("phoneBound").asBoolean());

        String audit = jdbc.queryForObject("""
                        SELECT CAST(JSON_ARRAYAGG(safe_change_summary) AS CHAR)
                        FROM ops_audit_log
                        WHERE tenant_id = ?
                        """, String.class, tenantId);
        assertNotNull(audit);
        assertFalse(audit.contains("13812345678"));
        assertFalse(audit.contains("fake-phone:"));
    }

    @Test
    void walletFailureRollsBackAndConcurrentFirstLoginConverges()
            throws Exception {
        participant.failAfterDelegate();
        login("fake:rollback:" + run, null, 500);
        assertUserAndWalletCounts(0, 0);
        assertEquals(0, organizationWalletEntryCounterCount());
        assertEquals(0, sessionCount());

        login("fake:rollback:" + run, null, 201);
        assertUserAndWalletCounts(1, 1);
        assertOrganizationWalletEntryCounter(0L, 0L);
        assertEquals(1, sessionCount());

        String concurrentCode = "fake:concurrent:" + run;
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<JsonNode> first = executor.submit(() -> {
                start.await();
                return login(concurrentCode, null, 201);
            });
            Future<JsonNode> second = executor.submit(() -> {
                start.await();
                return login(concurrentCode, null, 201);
            });
            start.countDown();
            JsonNode firstResult = first.get();
            JsonNode secondResult = second.get();
            assertEquals(
                    firstResult.path("subjectUid").asText(),
                    secondResult.path("subjectUid").asText());
            assertNotEquals(
                    firstResult.path("accessToken").asText(),
                    secondResult.path("accessToken").asText());
        } finally {
            executor.shutdownNow();
        }
        assertUserAndWalletCounts(2, 2);
        assertOrganizationWalletEntryCounter(0L, 0L);
        assertEquals(3, sessionCount());
    }

    @Test
    void newWalletInitializesOrganizationEntryCounterWithoutResettingIt()
            throws Exception {
        assertEquals(0, organizationWalletEntryCounterCount());

        login("fake:counter-first:" + run, null, 201);
        assertOrganizationWalletEntryCounter(0L, 0L);

        assertEquals(1, jdbc.update("""
                        UPDATE fund_organization_wallet_entry_counter
                        SET last_visibility_sequence_no = 7,
                            lock_version = 3,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        """,
                tenantId,
                organizationId));

        login("fake:counter-second:" + run, null, 201);
        login("fake:counter-second:" + run, null, 201);

        assertUserAndWalletCounts(2, 2);
        assertOrganizationWalletEntryCounter(7L, 3L);
    }

    @Test
    void concurrentCrossingRebindsUseStableLocksAndOnlyOneSucceeds()
            throws Exception {
        JsonNode firstUser = login(
                "fake:cross-user-a:" + run, null, 201);
        phoneBind(
                firstUser.path("accessToken").asText(),
                UUID.randomUUID(),
                "fake-phone:+8613900000001",
                201);
        JsonNode secondUser = login(
                "fake:cross-user-b:" + run, null, 201);
        phoneBind(
                secondUser.path("accessToken").asText(),
                UUID.randomUUID(),
                "fake-phone:+8613900000002",
                201);
        UUID firstUserUid = UUID.fromString(
                firstUser.path("subjectUid").asText());
        UUID secondUserUid = UUID.fromString(
                secondUser.path("subjectUid").asText());
        UUID secondStaffUid = createOrganizationManager("cross-second");
        PlatformActor secondOperator =
                createPlatformActor("cross-second");

        asPlatformActor();
        StaffMiniappBindingView firstBinding = bindingService.setBinding(
                UUID.randomUUID(),
                tenantCode,
                organizationCode,
                staffUid,
                new SetStaffMiniappBindingRequest(
                        firstUserUid, null, null, "initial A"));
        StaffMiniappBindingView secondBinding = bindingService.setBinding(
                UUID.randomUUID(),
                tenantCode,
                organizationCode,
                secondStaffUid,
                new SetStaffMiniappBindingRequest(
                        secondUserUid, null, null, "initial B"));
        clearContexts();

        CountDownLatch start = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<String> first = executor.submit(() -> crossingRebind(
                    start,
                    staffUid,
                    secondUserUid,
                    new BindingSnapshot(
                            firstBinding.bindingUid(),
                            firstBinding.version()),
                    new BindingSnapshot(
                            secondBinding.bindingUid(),
                            secondBinding.version()),
                    new PlatformActor(
                            platformAdminId, platformAdminUid)));
            Future<String> second = executor.submit(() -> crossingRebind(
                    start,
                    secondStaffUid,
                    firstUserUid,
                    new BindingSnapshot(
                            secondBinding.bindingUid(),
                            secondBinding.version()),
                    new BindingSnapshot(
                            firstBinding.bindingUid(),
                            firstBinding.version()),
                    secondOperator));
            start.countDown();
            assertEquals(
                    Set.of("SUCCESS", "API_409"),
                    Set.of(first.get(), second.get()));
        } finally {
            executor.shutdownNow();
        }
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM iam_staff_miniapp_binding
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND status = 'ACTIVE'
                        """,
                Integer.class,
                tenantId,
                organizationId));
    }

    @Test
    void sameWechatIdentityCreatesIndependentUsersAndWalletsAcrossOrganizations()
            throws Exception {
        String secondOrganizationCode = code("o2");
        String secondAppId = "wx" + UUID.randomUUID().toString()
                .replace("-", "").substring(0, 16);
        jdbc.update("""
                        INSERT INTO iam_organization (
                            tenant_id, organization_code,
                            organization_name, status,
                            contact_phone, contact_address,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, 'V02 second organization', 'ENABLED',
                            NULL, NULL, 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                tenantId,
                secondOrganizationCode);
        long secondOrganizationId = jdbc.queryForObject("""
                        SELECT id
                        FROM iam_organization
                        WHERE tenant_id = ?
                          AND organization_code = ?
                        """,
                Long.class,
                tenantId,
                secondOrganizationCode);
        jdbc.update("""
                        INSERT INTO iam_organization_miniapp (
                            tenant_id, organization_id, appid,
                            display_name, login_enabled, secret_ref,
                            activated_at, lock_version,
                            configured_at, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 'V02 second miniapp', 1,
                            'fake:credential', UTC_TIMESTAMP(3), 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3),
                            UTC_TIMESTAMP(3)
                        )
                        """,
                tenantId,
                secondOrganizationId,
                secondAppId);

        String sharedWechatCode = "fake:cross-org:" + run;
        JsonNode first = loginForApp(
                appId, sharedWechatCode, null, 201);
        JsonNode second = loginForApp(
                secondAppId, sharedWechatCode, null, 201);
        assertNotEquals(
                first.path("subjectUid").asText(),
                second.path("subjectUid").asText());

        phoneBind(
                first.path("accessToken").asText(),
                UUID.randomUUID(),
                "fake-phone:+8613711112222",
                201);
        phoneBind(
                second.path("accessToken").asText(),
                UUID.randomUUID(),
                "fake-phone:+8613711112222",
                201);

        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM iam_organization_user
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        """,
                Integer.class,
                tenantId,
                organizationId));
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM iam_organization_user
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        """,
                Integer.class,
                tenantId,
                secondOrganizationId));
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM fund_user_wallet
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND available_balance_cent = 0
                          AND frozen_withdrawal_cent = 0
                        """,
                Integer.class,
                tenantId,
                organizationId));
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM fund_user_wallet
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND available_balance_cent = 0
                          AND frozen_withdrawal_cent = 0
                        """,
                Integer.class,
                tenantId,
                secondOrganizationId));
    }

    @Test
    void organizationUserDirectoryMutationsPreserveScopeAndRevokeSessions()
            throws Exception {
        String wxLoginCode = "fake:directory:" + run;
        JsonNode first = login(wxLoginCode, deploymentCode, 201);
        UUID userUid = UUID.fromString(
                first.path("subjectUid").asText());
        String ordinaryToken = first.path("accessToken").asText();

        asPlatformActor();
        PageData<OrganizationUserView> initial =
                bindingService.listOrganizationUsers(
                        tenantCode,
                        organizationCode,
                        1,
                        20,
                        "ACTIVE",
                        false,
                        null,
                        null,
                        deploymentCode,
                        false);
        assertEquals(1, initial.total());
        OrganizationUserView initialUser = initial.items().getFirst();
        assertEquals(userUid, initialUser.organizationUserUid());
        assertFalse(initialUser.phoneBound());
        assertEquals(
                deploymentCode,
                initialUser.registrationSource().deploymentCode());
        assertFalse(initialUser.cleanOperationEnabled());
        assertEquals(0, initialUser.version());
        assertEquals(0, initialUser.authVersion());

        UUID grantOperation = UUID.randomUUID();
        OrganizationUserView granted =
                bindingService.grantCleanOperation(
                        grantOperation,
                        tenantCode,
                        organizationCode,
                        userUid,
                        new AccountVersionCommand(
                                0L, 0L, "grant cleaner"));
        assertTrue(granted.cleanOperationEnabled());
        assertEquals(1, granted.version());
        assertEquals(1, granted.authVersion());
        OrganizationUserView grantReplay =
                bindingService.grantCleanOperation(
                        grantOperation,
                        tenantCode,
                        organizationCode,
                        userUid,
                        new AccountVersionCommand(
                                0L, 0L, "grant cleaner"));
        assertEquals(granted, grantReplay);
        bearerGet(
                ordinaryToken,
                "/api/v1/miniapp/auth/sessions/current",
                401);

        JsonNode cleanerLogin = login(wxLoginCode, null, 201);
        assertEquals(
                "CLEANING",
                cleanerLogin.path("entryMode").asText());
        String cleanerToken =
                cleanerLogin.path("accessToken").asText();

        OrganizationUserView frozen =
                bindingService.freezeOrganizationUser(
                        UUID.randomUUID(),
                        tenantCode,
                        organizationCode,
                        userUid,
                        new AccountVersionCommand(
                                1L, 1L, "temporary freeze"));
        assertEquals("FROZEN", frozen.status());
        assertTrue(frozen.cleanOperationEnabled());
        assertEquals(2, frozen.version());
        assertEquals(2, frozen.authVersion());
        bearerGet(
                cleanerToken,
                "/api/v1/miniapp/auth/sessions/current",
                401);
        JsonNode frozenLogin = login(wxLoginCode, null, 403);
        assertEquals(
                "IDENTITY.ORGANIZATION_USER_FROZEN",
                frozenLogin.path("code").asText());

        TargetApiException duplicateFreeze = assertThrows(
                TargetApiException.class,
                () -> bindingService.freezeOrganizationUser(
                        UUID.randomUUID(),
                        tenantCode,
                        organizationCode,
                        userUid,
                        new AccountVersionCommand(
                                2L, 2L, "duplicate freeze")));
        assertEquals(422, duplicateFreeze.status());
        assertEquals(
                "IDENTITY.ORGANIZATION_USER_ALREADY_FROZEN",
                duplicateFreeze.code());

        OrganizationUserView restored =
                bindingService.restoreOrganizationUser(
                        UUID.randomUUID(),
                        tenantCode,
                        organizationCode,
                        userUid,
                        new AccountVersionCommand(
                                2L, 2L, "restore"));
        assertEquals("ACTIVE", restored.status());
        assertTrue(restored.cleanOperationEnabled());
        assertEquals(3, restored.version());
        assertEquals(3, restored.authVersion());

        JsonNode restoredLogin = login(wxLoginCode, null, 201);
        assertEquals(
                "CLEANING",
                restoredLogin.path("entryMode").asText());
        String restoredToken =
                restoredLogin.path("accessToken").asText();

        TargetApiException stale = assertThrows(
                TargetApiException.class,
                () -> bindingService.revokeCleanOperation(
                        UUID.randomUUID(),
                        tenantCode,
                        organizationCode,
                        userUid,
                        new AccountVersionCommand(
                                2L, 2L, "stale revoke")));
        assertEquals(409, stale.status());
        assertEquals("COMMON.VERSION_CONFLICT", stale.code());

        OrganizationUserView revoked =
                bindingService.revokeCleanOperation(
                        UUID.randomUUID(),
                        tenantCode,
                        organizationCode,
                        userUid,
                        new AccountVersionCommand(
                                3L, 3L, "revoke cleaner"));
        assertFalse(revoked.cleanOperationEnabled());
        assertEquals(4, revoked.version());
        assertEquals(4, revoked.authVersion());
        bearerGet(
                restoredToken,
                "/api/v1/miniapp/auth/sessions/current",
                401);
        JsonNode ordinaryAgain = login(wxLoginCode, null, 201);
        assertEquals("USER", ordinaryAgain.path("entryMode").asText());
    }

    private String crossingRebind(
            CountDownLatch start,
            UUID targetStaffUid,
            UUID targetUserUid,
            BindingSnapshot expectedStaffBinding,
            BindingSnapshot expectedUserBinding,
            PlatformActor operator) throws Exception {
        start.await();
        asPlatformActor(operator.id(), operator.uid());
        try {
            bindingService.setBinding(
                    UUID.randomUUID(),
                    tenantCode,
                    organizationCode,
                    targetStaffUid,
                    new SetStaffMiniappBindingRequest(
                            targetUserUid,
                            expectedStaffBinding,
                            expectedUserBinding,
                            "crossing rebind"));
            return "SUCCESS";
        } catch (TargetApiException exception) {
            return "API_" + exception.status();
        } finally {
            clearContexts();
        }
    }

    private JsonNode login(
            String wxLoginCode,
            String source,
            int expectedStatus) throws Exception {
        return loginForApp(
                appId, wxLoginCode, source, expectedStatus);
    }

    private JsonNode loginForApp(
            String targetAppId,
            String wxLoginCode,
            String source,
            int expectedStatus) throws Exception {
        Map<String, Object> body = source == null
                ? Map.of(
                        "appId", targetAppId,
                        "wxLoginCode", wxLoginCode)
                : Map.of(
                        "appId", targetAppId,
                        "wxLoginCode", wxLoginCode,
                        "registrationSource",
                        Map.of("deploymentCode", source));
        MvcResult result = mockMvc.perform(
                        post("/api/v1/miniapp/auth/sessions")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(objectMapper.writeValueAsBytes(body)))
                .andReturn();
        assertEquals(expectedStatus, result.getResponse().getStatus(),
                result.getResponse().getContentAsString());
        JsonNode root = json(result);
        return expectedStatus < 300 ? root.path("data") : root;
    }

    private JsonNode phoneBind(
            String token,
            UUID operationUid,
            String phoneCode,
            int expectedStatus) throws Exception {
        MvcResult result = mockMvc.perform(
                        post("/api/v1/miniapp/me/phone-bindings")
                                .header("Authorization", "Bearer " + token)
                                .header(
                                        "Idempotency-Key",
                                        operationUid.toString())
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(objectMapper.writeValueAsBytes(
                                        Map.of(
                                                "wechatPhoneCode",
                                                phoneCode))))
                .andReturn();
        assertEquals(expectedStatus, result.getResponse().getStatus(),
                result.getResponse().getContentAsString());
        JsonNode root = json(result);
        return expectedStatus < 300 ? root.path("data") : root;
    }

    private void bearerGet(
            String token,
            String path,
            int expectedStatus) throws Exception {
        MvcResult result = mockMvc.perform(
                        get(path).header(
                                "Authorization", "Bearer " + token))
                .andReturn();
        assertEquals(expectedStatus, result.getResponse().getStatus(),
                result.getResponse().getContentAsString());
    }

    private void asPlatformActor() {
        asPlatformActor(platformAdminId, platformAdminUid);
    }

    private void asPlatformActor(long actorId, UUID actorUid) {
        TargetWebActorContext.set(new TargetWebActor(
                WebAccountType.PLATFORM_ADMIN,
                TrustedAudience.WEB_PLATFORM,
                actorId,
                actorUid,
                null,
                null,
                null,
                UUID.randomUUID(),
                "V02 operator",
                null,
                0,
                0,
                java.time.Instant.now().plusSeconds(3600),
                Set.of(),
                List.of()));
    }

    private PlatformActor createPlatformActor(String suffix) {
        UUID uid = UUID.randomUUID();
        jdbc.update("""
                        INSERT INTO iam_platform_admin (
                            platform_admin_uid, login_name, password_hash,
                            display_name, enabled, failed_login_count,
                            locked_until, auth_version, password_changed_at,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, 'unused-test-hash', 'V02 second operator',
                            1, 0, NULL, 0, UTC_TIMESTAMP(3), 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                uid.toString(),
                "v02-" + suffix + "-" + run);
        long id = jdbc.queryForObject("""
                        SELECT id
                        FROM iam_platform_admin
                        WHERE platform_admin_uid = ?
                        """,
                Long.class,
                uid.toString());
        return new PlatformActor(id, uid);
    }

    private UUID createOrganizationManager(String suffix) {
        UUID uid = UUID.randomUUID();
        jdbc.update("""
                        INSERT INTO iam_staff_account (
                            tenant_id, staff_account_uid, account_kind,
                            login_name, password_hash, display_name,
                            contact_phone, enabled, failed_login_count,
                            locked_until, auth_version, password_changed_at,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, 'STAFF', ?,
                            'unused-test-hash', 'V02 organization manager',
                            NULL, 1, 0, NULL, 0, UTC_TIMESTAMP(3),
                            0, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                tenantId,
                uid.toString(),
                "v02-" + suffix + "-" + run);
        long staffId = jdbc.queryForObject("""
                        SELECT id
                        FROM iam_staff_account
                        WHERE tenant_id = ?
                          AND staff_account_uid = ?
                        """,
                Long.class,
                tenantId,
                uid.toString());
        jdbc.update("""
                        INSERT INTO iam_organization_staff_membership (
                            tenant_id, organization_id, staff_account_id,
                            is_manager, enabled, lock_version,
                            created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 1, 1, 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                tenantId,
                organizationId,
                staffId);
        return uid;
    }

    private void assertUserAndWalletCounts(
            int expectedUsers,
            int expectedWallets) {
        assertEquals(expectedUsers, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM iam_organization_user
                        WHERE organization_miniapp_id = ?
                        """, Integer.class, miniappId));
        assertEquals(expectedWallets, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM fund_user_wallet
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        """, Integer.class, tenantId, organizationId));
        assertEquals(0L, jdbc.queryForObject("""
                        SELECT COALESCE(SUM(available_balance_cent), 0)
                        FROM fund_user_wallet
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        """, Long.class, tenantId, organizationId));
    }

    private int sessionCount() {
        return jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM iam_organization_user_session
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        """, Integer.class, tenantId, organizationId);
    }

    private int organizationWalletEntryCounterCount() {
        return jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM fund_organization_wallet_entry_counter
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        """,
                Integer.class,
                tenantId,
                organizationId);
    }

    private void assertOrganizationWalletEntryCounter(
            long expectedLastVisibilitySequenceNo,
            long expectedLockVersion) {
        Map<String, Object> counter = jdbc.queryForMap("""
                        SELECT last_visibility_sequence_no, lock_version
                        FROM fund_organization_wallet_entry_counter
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        """,
                tenantId,
                organizationId);
        assertEquals(
                expectedLastVisibilitySequenceNo,
                ((Number) counter.get("last_visibility_sequence_no"))
                        .longValue());
        assertEquals(
                expectedLockVersion,
                ((Number) counter.get("lock_version")).longValue());
        assertEquals(1, organizationWalletEntryCounterCount());
    }

    private JsonNode json(MvcResult result) throws Exception {
        String content = result.getResponse().getContentAsString();
        return content.isBlank()
                ? objectMapper.createObjectNode()
                : objectMapper.readTree(content);
    }

    private String code(String prefix) {
        return prefix + "-" + run;
    }

    private record PlatformActor(long id, UUID uid) {
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class ProbeConfiguration {

        @Bean
        @Primary
        ProbeParticipant v02ProbeParticipant(
                @Qualifier("legacyEmbeddedWalletRegistrationParticipant")
                OrganizationUserRegistrationParticipant delegate) {
            return new ProbeParticipant(delegate);
        }
    }

    static final class ProbeParticipant
            implements OrganizationUserRegistrationParticipant {

        private final OrganizationUserRegistrationParticipant delegate;
        private final AtomicBoolean failAfterDelegate = new AtomicBoolean();

        ProbeParticipant(OrganizationUserRegistrationParticipant delegate) {
            this.delegate = delegate;
        }

        @Override
        public void initializeWallet(
                OrganizationUserRegistrationCommand command) {
            delegate.initializeWallet(command);
            if (failAfterDelegate.compareAndSet(true, false)) {
                throw new IllegalStateException(
                        "injected wallet participant failure");
            }
        }

        void failAfterDelegate() {
            failAfterDelegate.set(true);
        }

        void reset() {
            failAfterDelegate.set(false);
        }
    }
}
