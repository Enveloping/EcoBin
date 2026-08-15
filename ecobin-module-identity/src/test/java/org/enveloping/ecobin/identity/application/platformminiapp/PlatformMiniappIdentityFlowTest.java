package org.enveloping.ecobin.identity.application.platformminiapp;

import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.FactoryOperatorPersistenceRef;
import org.enveloping.ecobin.identity.api.port.FactoryMiniappAuthorizationPort;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappBindingTokenService.IssuedToken;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappRepository.BindingIntentRow;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappRepository.FactoryOperatorRow;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappRepository.MiniappChannelRow;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.application.web.WebAccountType;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionStatus;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.json.JsonMapper;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class PlatformMiniappIdentityFlowTest {

    private static final long OPERATOR_ID = 41;
    private static final UUID OPERATOR_UID =
            UUID.fromString("11111111-1111-4111-8111-111111111111");
    private static final UUID WEB_SESSION_UID =
            UUID.fromString("22222222-2222-4222-8222-222222222222");
    private static final String APP_ID = "wx1234567890abcdef";
    private static final Instant ACTIVATED_AT =
            Instant.parse("2026-01-01T00:00:00Z");

    private InMemoryPlatformMiniappRepository repository;
    private PlatformMiniappBindingTokenService tokens;
    private RecordingAuditPort audit;
    private JwtTokenProvider tokenProvider;
    private PlatformMiniappLoginTransactionService login;
    private PlatformMiniappSessionService sessions;
    private FactoryOperatorPersistenceRef persistenceRef;
    private FactoryOperatorPersistenceRefFactory referenceFactory;

    @BeforeEach
    void setUp() {
        repository = new InMemoryPlatformMiniappRepository();
        repository.operator = activeOperator();
        repository.channel = new MiniappChannelRow(
                51, APP_ID, "app-secret", true, ACTIVATED_AT);
        tokens = new PlatformMiniappBindingTokenService();
        audit = new RecordingAuditPort();
        tokenProvider = new JwtTokenProvider(
                "factory-miniapp-test-secret-that-is-at-least-256-bits-long");
        login = new PlatformMiniappLoginTransactionService(
                repository,
                tokens,
                tokenProvider,
                Duration.ofHours(2));
        sessions = new PlatformMiniappSessionService(
                repository, tokenProvider);
        persistenceRef = mock(FactoryOperatorPersistenceRef.class);
        referenceFactory = factoryOperatorKey -> {
            assertEquals(OPERATOR_ID, factoryOperatorKey);
            return persistenceRef;
        };
        TargetWebActorContext.set(webActor());
    }

    @AfterEach
    void tearDown() {
        TargetWebActorContext.clear();
        PlatformMiniappActorContext.clear();
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
        TransactionSynchronizationManager
                .setActualTransactionActive(false);
    }

    @Test
    void webCreatesOfficialFiveMinuteCodeWithoutReturningRawToken() {
        AtomicReference<String> generatedScene = new AtomicReference<>();
        var codes = (org.enveloping.ecobin.identity.api.port
                .WechatMiniProgramCodePort) (appId, appSecret, page, scene) -> {
            assertEquals(APP_ID, appId);
            assertEquals("app-secret", appSecret);
            assertEquals("factory/pages/bind/bind", page);
            generatedScene.set(scene);
            return new byte[]{1, 2, 3};
        };
        PlatformTransactionManager manager = mock(
                PlatformTransactionManager.class);
        when(manager.getTransaction(any())).thenReturn(
                mock(TransactionStatus.class));
        var intents = new PlatformMiniappBindingIntentService(
                repository,
                tokens,
                codes,
                audit,
                JsonMapper.builder().findAndAddModules().build(),
                manager);

        var created = intents.create(OPERATOR_UID);

        assertTrue(created.expiresAt().isAfter(
                Instant.now().plusSeconds(298)));
        assertTrue(created.expiresAt().isBefore(
                Instant.now().plusSeconds(301)));
        assertTrue(created.miniProgramCodeDataUrl().startsWith(
                "data:image/png;base64,"));
        assertNotNull(generatedScene.get());
        assertEquals(22, generatedScene.get().length());
        assertFalse(created.miniProgramCodeDataUrl().contains(
                generatedScene.get()));
        assertEquals(1, audit.entries.size());
        assertFalse(audit.entries.getFirst().safeChangeSummaryJson()
                .contains(generatedScene.get()));
    }

    @Test
    void firstBindingThenTokenlessLoginAndSessionLifecycleWork() {
        IssuedToken intent = pendingIntent(Instant.now().plusSeconds(300));

        var firstLogin = login.completeLogin(
                APP_ID, "openid-one", intent.value());

        assertTrue(firstLogin.newlyBound());
        assertEquals("miniapp-factory", firstLogin.audience());
        assertEquals("FACTORY_ACCEPTANCE", firstLogin.entryMode());
        assertEquals(OPERATOR_UID, firstLogin.factoryOperatorUid());
        assertEquals("FACTORY-001", firstLogin.operatorCode());
        assertEquals(List.of(
                        FactoryMiniappAuthorizationPort.ACCEPTANCE_READ,
                        FactoryMiniappAuthorizationPort.BAG_INSTALL,
                        FactoryMiniappAuthorizationPort.BAG_CORRECT),
                firstLogin.capabilities());
        BindingIntentRow consumed = repository.findBindingIntent(
                        tokens.digest(intent.value()), false)
                .orElseThrow();
        assertEquals("CONSUMED", consumed.status());
        assertNotNull(consumed.consumedWechatSubjectId());
        var claims = tokenProvider.parseTargetFactoryMiniappSession(
                firstLogin.accessToken());
        assertEquals(OPERATOR_UID, claims.principalUid());
        assertThrows(
                RuntimeException.class,
                () -> tokenProvider.parseTargetMiniappSession(
                        firstLogin.accessToken()));

        PlatformMiniappActor actor = sessions.resolve(
                firstLogin.accessToken());
        PlatformMiniappActorContext.set(actor);
        TransactionSynchronizationManager
                .setActualTransactionActive(true);
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(true);
        var authorized = new PlatformMiniappAuthorizationService(
                repository, referenceFactory)
                .requireCapability(
                        FactoryMiniappAuthorizationPort.ACCEPTANCE_READ);
        assertEquals(OPERATOR_UID, authorized.factoryOperatorUid());
        assertEquals("FACTORY-001", authorized.operatorCode());
        assertSame(persistenceRef, authorized.persistenceRef());
        PlatformMiniappActorContext.clear();

        var laterLogin = login.completeLogin(
                APP_ID, "openid-one", null);
        assertFalse(laterLogin.newlyBound());
        assertEquals(OPERATOR_UID, laterLogin.factoryOperatorUid());

        sessions.revokeCurrent(actor);
        TargetApiException revoked = assertThrows(
                TargetApiException.class,
                () -> sessions.resolve(firstLogin.accessToken()));
        assertEquals("AUTH.SESSION_INVALID", revoked.code());
    }

    @Test
    void consumedTokenMayReplayOnlyForTheSameWechatSubject() {
        IssuedToken intent = pendingIntent(Instant.now().plusSeconds(300));
        login.completeLogin(APP_ID, "openid-one", intent.value());

        var safeReplay = login.completeLogin(
                APP_ID, "openid-one", intent.value());
        assertFalse(safeReplay.newlyBound());

        TargetApiException stolenReplay = assertThrows(
                TargetApiException.class,
                () -> login.completeLogin(
                        APP_ID, "openid-two", intent.value()));
        assertEquals(
                "IDENTITY.FACTORY_BINDING_TOKEN_INVALID",
                stolenReplay.code());
    }

    @Test
    void bindingExpiryAndCurrentOperatorStateAreEnforced() {
        IssuedToken expired = pendingIntent(Instant.now().minusSeconds(1));
        TargetApiException expiry = assertThrows(
                TargetApiException.class,
                () -> login.completeLogin(
                        APP_ID, "openid-expired", expired.value()));
        assertEquals(
                "IDENTITY.FACTORY_BINDING_TOKEN_EXPIRED",
                expiry.code());

        IssuedToken valid = pendingIntent(Instant.now().plusSeconds(300));
        var created = login.completeLogin(
                APP_ID, "openid-one", valid.value());
        assertNotNull(sessions.resolve(created.accessToken()));
        repository.operator = new FactoryOperatorRow(
                OPERATOR_ID,
                OPERATOR_UID,
                "FACTORY-001",
                "厂家操作员",
                false,
                8,
                1);
        TargetApiException disabled = assertThrows(
                TargetApiException.class,
                () -> sessions.resolve(created.accessToken()));
        assertEquals("AUTH.SESSION_INVALID", disabled.code());
    }

    @Test
    void malformedTokensAndMissingCapabilityAreRejected() {
        for (String malformed : List.of(
                "",
                "not-base64url",
                "AAAAAAAAAAAAAAAAAAAAAA==")) {
            TargetApiException failure = assertThrows(
                    TargetApiException.class,
                    () -> tokens.digest(malformed));
            assertEquals(
                    "IDENTITY.FACTORY_BINDING_TOKEN_INVALID",
                    failure.code());
        }

        PlatformMiniappActorContext.set(new PlatformMiniappActor(
                OPERATOR_ID,
                OPERATOR_UID,
                "FACTORY-001",
                UUID.randomUUID(),
                7,
                Instant.now().plusSeconds(60),
                "厂家操作员",
                Set.of()));
        TargetApiException forbidden = assertThrows(
                TargetApiException.class,
                () -> new PlatformMiniappAuthorizationService(
                        repository, referenceFactory)
                        .requireCapability(
                                FactoryMiniappAuthorizationPort
                                        .ACCEPTANCE_READ));
        assertEquals(403, forbidden.status());
    }

    private IssuedToken pendingIntent(Instant expiresAt) {
        IssuedToken issued = tokens.issue();
        repository.insertBindingIntent(
                UUID.randomUUID(),
                OPERATOR_ID,
                repository.channel.id(),
                1,
                issued.sha256(),
                expiresAt,
                Instant.now());
        return issued;
    }

    private static FactoryOperatorRow activeOperator() {
        return new FactoryOperatorRow(
                OPERATOR_ID,
                OPERATOR_UID,
                "FACTORY-001",
                "厂家操作员",
                true,
                7,
                0);
    }

    private static TargetWebActor webActor() {
        return new TargetWebActor(
                WebAccountType.PLATFORM_ADMIN,
                TrustedAudience.WEB_PLATFORM,
                1,
                UUID.fromString("33333333-3333-4333-8333-333333333333"),
                null,
                null,
                null,
                WEB_SESSION_UID,
                "平台管理员",
                null,
                0,
                7,
                Instant.parse("2030-01-01T00:00:00Z"),
                Set.of("platform-admin.manage"),
                List.of());
    }

    private static final class RecordingAuditPort implements AuditPort {

        private final List<AuditEntry> entries = new ArrayList<>();

        @Override
        public Optional<SuccessfulAudit> findSuccessful(UUID operationUid) {
            return Optional.empty();
        }

        @Override
        public long append(AuditEntry entry) {
            entries.add(entry);
            return entries.size();
        }
    }
}
