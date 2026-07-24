package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.identity.api.command.OrganizationUserRegistrationCommand;
import org.enveloping.ecobin.identity.api.legacy.LegacyMiniappRegistrationPort;
import org.enveloping.ecobin.identity.api.legacy.LegacyTenantDirectoryPort;
import org.enveloping.ecobin.identity.api.legacy.LegacyTenantDraft;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationParticipant;
import org.enveloping.ecobin.identity.api.port.WechatSessionPort;
import org.enveloping.ecobin.identity.api.result.WechatSession;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@Import(IdentityRegistrationTransactionTest.FaultInjectionConfiguration.class)
class IdentityRegistrationTransactionTest {

    @Autowired
    private LegacyMiniappRegistrationPort registrationPort;
    @Autowired
    private LegacyTenantDirectoryPort tenantDirectory;
    @Autowired
    private ProbeRegistrationParticipant participant;
    @Autowired
    private ControllableJwtTokenProvider tokenProvider;
    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private TransactionObservingWechatSessionPort wechatSessionPort;

    @BeforeEach
    void setUp() {
        TenantContextHolder.setTenantId(1L);
        TenantContextHolder.setIgnore(true);
        participant.reset();
        tokenProvider.reset();
        wechatSessionPort.reset();
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
    }

    @Test
    void firstRegistrationInvokesParticipantOnceAndRepeatDoesNotInvokeAgain() {
        String openid = "openid-registration-repeat-" + System.nanoTime();

        var first = registrationPort.registerOrFind(2L, openid, null);
        var repeat = registrationPort.registerOrFind(2L, openid, null);

        assertTrue(first.newRegistration());
        assertTrue(!repeat.newRegistration());
        assertEquals(first.userId(), repeat.userId());
        assertEquals(1, participant.invocationCount());
    }

    @Test
    void participantFailureRollsBackIdentityAndRetryIsAFirstRegistration() {
        String openid = "openid-registration-rollback-" + System.nanoTime();
        participant.failNext();

        assertThrows(IllegalStateException.class,
                () -> registrationPort.registerOrFind(2L, openid, null));

        var retry = registrationPort.registerOrFind(2L, openid, null);
        assertTrue(retry.newRegistration());
        assertEquals(2, participant.invocationCount());
    }

    @Test
    void localSessionFailureRollsBackFirstIdentityAndParticipant() throws Exception {
        String appid = "wx-registration-" + System.nanoTime();
        tenantDirectory.create(new LegacyTenantDraft(
                "登录事务测试", "LOGIN-" + System.nanoTime(), "login-" + System.nanoTime(),
                "password", appid, "secret", null, null, null, null, 1));
        tokenProvider.failNext();
        String body = "{\"code\":\"session-failure\",\"appid\":\"" + appid + "\"}";

        mockMvc.perform(post("/api/system/auth/wx-login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().is5xxServerError());

        mockMvc.perform(post("/api/system/auth/wx-login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk());

        assertEquals(2, participant.invocationCount(),
                "JWT 构造失败后重试必须重新执行首次注册参与者");
        assertTrue(!wechatSessionPort.observedActiveTransaction(),
                "code2session must finish before the local registration transaction starts");
    }

    @Test
    void code2sessionRunsOutsideLocalDatabaseTransaction() throws Exception {
        String appid = "wx-outtx-" + System.nanoTime();
        tenantDirectory.create(new LegacyTenantDraft(
                "外调事务边界测试", "EXTERNAL-" + System.nanoTime(),
                "external-" + System.nanoTime(),
                "password", appid, "secret", null, null, null, null, 1));

        mockMvc.perform(post("/api/system/auth/wx-login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"code\":\"outside-transaction\",\"appid\":\""
                                + appid + "\"}"))
                .andExpect(status().isOk());

        assertEquals(1, wechatSessionPort.invocationCount());
        assertTrue(!wechatSessionPort.observedActiveTransaction(),
                "code2session must not run while a local database transaction is active");
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class FaultInjectionConfiguration {

        @Bean
        @Primary
        ProbeRegistrationParticipant probeRegistrationParticipant(
                @Qualifier("legacyEmbeddedWalletRegistrationParticipant")
                OrganizationUserRegistrationParticipant delegate) {
            return new ProbeRegistrationParticipant(delegate);
        }

        @Bean
        @Primary
        ControllableJwtTokenProvider controllableJwtTokenProvider() {
            return new ControllableJwtTokenProvider();
        }

        @Bean
        @Primary
        TransactionObservingWechatSessionPort deterministicWechatSessionPort() {
            return new TransactionObservingWechatSessionPort();
        }
    }

    static final class ProbeRegistrationParticipant
            implements OrganizationUserRegistrationParticipant {

        private final OrganizationUserRegistrationParticipant delegate;
        private final AtomicInteger invocationCount = new AtomicInteger();
        private final AtomicBoolean failNext = new AtomicBoolean();

        ProbeRegistrationParticipant(OrganizationUserRegistrationParticipant delegate) {
            this.delegate = delegate;
        }

        @Override
        public void initializeWallet(OrganizationUserRegistrationCommand command) {
            invocationCount.incrementAndGet();
            if (failNext.compareAndSet(true, false)) {
                throw new IllegalStateException("injected participant failure");
            }
            delegate.initializeWallet(command);
        }

        void failNext() {
            failNext.set(true);
        }

        int invocationCount() {
            return invocationCount.get();
        }

        void reset() {
            invocationCount.set(0);
            failNext.set(false);
        }
    }

    static final class TransactionObservingWechatSessionPort implements WechatSessionPort {

        private final AtomicInteger invocationCount = new AtomicInteger();
        private final AtomicBoolean observedActiveTransaction = new AtomicBoolean();

        @Override
        public WechatSession exchange(String appid, String secret, String code) {
            invocationCount.incrementAndGet();
            if (TransactionSynchronizationManager.isActualTransactionActive()) {
                observedActiveTransaction.set(true);
            }
            return new WechatSession("openid-" + code, "unionid-" + code);
        }

        int invocationCount() {
            return invocationCount.get();
        }

        boolean observedActiveTransaction() {
            return observedActiveTransaction.get();
        }

        void reset() {
            invocationCount.set(0);
            observedActiveTransaction.set(false);
        }
    }

    static final class ControllableJwtTokenProvider extends JwtTokenProvider {

        private final AtomicBoolean failNext = new AtomicBoolean();

        ControllableJwtTokenProvider() {
            super("TEST_SECRET_KEY_MUST_BE_AT_LEAST_256_BITS_LONG_FOR_F02", 86_400_000);
        }

        @Override
        public String generateToken(Long userId, String subject, Long tenantId, Integer role) {
            if (failNext.compareAndSet(true, false)) {
                throw new IllegalStateException("injected local session failure");
            }
            return super.generateToken(userId, subject, tenantId, role);
        }

        void failNext() {
            failNext.set(true);
        }

        void reset() {
            failNext.set(false);
        }
    }
}
