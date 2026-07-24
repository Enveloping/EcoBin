package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.identity.api.command.OrganizationUserRegistrationCommand;
import org.enveloping.ecobin.identity.api.legacy.LegacyMiniappRegistrationPort;
import org.enveloping.ecobin.identity.api.persistence.OrganizationUserWalletOwnerRef;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationParticipant;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

import java.io.Serializable;
import java.lang.reflect.Modifier;
import java.util.Arrays;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest
@ActiveProfiles("test")
@Import(IdentityPersistenceReferenceTest.ReferenceProbeConfiguration.class)
class IdentityPersistenceReferenceTest {

    @Autowired
    private LegacyMiniappRegistrationPort registrationPort;
    @Autowired
    private ReferenceProbeParticipant participant;
    @Autowired
    private PlatformTransactionManager transactionManager;

    @BeforeEach
    void setUp() {
        TenantContextHolder.setTenantId(1L);
        TenantContextHolder.setIgnore(true);
        participant.reset();
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
    }

    @Test
    void referenceIsRedactedNonSerializableSingleUseAndBoundAfterCompletion() {
        participant.prepare(ProbeMode.SINGLE_USE);
        registrationPort.registerOrFind(
                2L, "openid-ref-single-" + System.nanoTime(), null);

        OrganizationUserWalletOwnerRef reference = participant.captured();
        assertNotNull(reference);
        assertEquals("OrganizationUserWalletOwnerRef[REDACTED]", reference.toString());
        assertFalse(Serializable.class.isAssignableFrom(OrganizationUserWalletOwnerRef.class));
        assertEquals(0, OrganizationUserWalletOwnerRef.class.getConstructors().length,
                "FK reference must not expose a public constructor");
        assertTrue(Arrays.stream(OrganizationUserWalletOwnerRef.class.getDeclaredMethods())
                        .noneMatch(method -> Modifier.isPublic(method.getModifiers())
                                && Modifier.isStatic(method.getModifiers())
                                && method.getReturnType() == OrganizationUserWalletOwnerRef.class),
                "FK reference must not expose a public static factory");

        IllegalStateException repeated = participant.sameTransactionFailure();
        assertNotNull(repeated);
        assertTrue(repeated.getMessage().contains("already consumed"));

        TransactionTemplate newTransaction = new TransactionTemplate(transactionManager);
        IllegalStateException escaped = assertThrows(IllegalStateException.class,
                () -> newTransaction.executeWithoutResult(status ->
                        reference.writeForeignKeyTo((tenant, organization, user) -> {
                        })));
        assertTrue(escaped.getMessage().contains("issuing transaction"),
                "after completion the reference must reject even inside a new transaction");

        assertSerializationRejectedOrEmpty(reference);
    }

    @Test
    void referenceCannotBeConsumedFromRequiresNewButRemainsValidInOuterTransaction() {
        participant.prepare(ProbeMode.REQUIRES_NEW);
        registrationPort.registerOrFind(
                2L, "openid-ref-requires-new-" + System.nanoTime(), null);

        IllegalStateException failure = participant.requiresNewFailure();
        assertNotNull(failure);
        assertTrue(failure.getMessage().contains("cross transactions"));
        assertNotNull(participant.sameTransactionFailure(),
                "delegate must still consume the reference in the resumed outer transaction");
    }

    @Test
    void referenceCannotCrossThreadWhileIssuingTransactionIsActive() {
        participant.prepare(ProbeMode.CROSS_THREAD);
        registrationPort.registerOrFind(
                2L, "openid-ref-cross-thread-" + System.nanoTime(), null);

        CompletionException failure = participant.crossThreadFailure();
        assertNotNull(failure);
        assertTrue(failure.getCause() instanceof IllegalStateException);
        assertTrue(failure.getCause().getMessage().contains("cross threads"));
        assertNotNull(participant.sameTransactionFailure(),
                "delegate must still consume the reference on the issuing thread");
    }

    private static void assertSerializationRejectedOrEmpty(
            OrganizationUserWalletOwnerRef reference) {
        try {
            String json = JsonMapper.builder().build().writeValueAsString(reference);
            assertEquals("{}", json,
                    "a transport serializer must not expose any internal key component");
        } catch (Exception expectedRejection) {
            // Rejecting transport serialization is the preferred safe outcome.
        }
    }

    enum ProbeMode {
        SINGLE_USE,
        REQUIRES_NEW,
        CROSS_THREAD
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class ReferenceProbeConfiguration {

        @Bean
        @Primary
        ReferenceProbeParticipant referenceProbeParticipant(
                @Qualifier("legacyEmbeddedWalletRegistrationParticipant")
                OrganizationUserRegistrationParticipant delegate,
                PlatformTransactionManager transactionManager) {
            return new ReferenceProbeParticipant(delegate, transactionManager);
        }
    }

    static final class ReferenceProbeParticipant
            implements OrganizationUserRegistrationParticipant {

        private final OrganizationUserRegistrationParticipant delegate;
        private final PlatformTransactionManager transactionManager;
        private final AtomicReference<ProbeMode> mode = new AtomicReference<>();
        private final AtomicReference<OrganizationUserWalletOwnerRef> captured =
                new AtomicReference<>();
        private final AtomicReference<IllegalStateException> sameTransactionFailure =
                new AtomicReference<>();
        private final AtomicReference<IllegalStateException> requiresNewFailure =
                new AtomicReference<>();
        private final AtomicReference<CompletionException> crossThreadFailure =
                new AtomicReference<>();

        ReferenceProbeParticipant(
                OrganizationUserRegistrationParticipant delegate,
                PlatformTransactionManager transactionManager) {
            this.delegate = delegate;
            this.transactionManager = transactionManager;
        }

        @Override
        public void initializeWallet(OrganizationUserRegistrationCommand command) {
            OrganizationUserWalletOwnerRef reference = command.walletOwnerRef();
            captured.set(reference);

            if (mode.get() == ProbeMode.REQUIRES_NEW) {
                TransactionTemplate requiresNew = new TransactionTemplate(transactionManager);
                requiresNew.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
                try {
                    requiresNew.executeWithoutResult(status ->
                            reference.writeForeignKeyTo((tenant, organization, user) -> {
                            }));
                    throw new AssertionError("REQUIRES_NEW unexpectedly consumed the reference");
                } catch (IllegalStateException expected) {
                    requiresNewFailure.set(expected);
                }
            } else if (mode.get() == ProbeMode.CROSS_THREAD) {
                try {
                    CompletableFuture.runAsync(() ->
                            reference.writeForeignKeyTo((tenant, organization, user) -> {
                            })).join();
                    throw new AssertionError("another thread unexpectedly consumed the reference");
                } catch (CompletionException expected) {
                    crossThreadFailure.set(expected);
                }
            }

            delegate.initializeWallet(command);
            try {
                reference.writeForeignKeyTo((tenant, organization, user) -> {
                });
                throw new AssertionError("reference unexpectedly allowed a second consumption");
            } catch (IllegalStateException expected) {
                sameTransactionFailure.set(expected);
            }
        }

        void prepare(ProbeMode value) {
            reset();
            mode.set(value);
        }

        OrganizationUserWalletOwnerRef captured() {
            return captured.get();
        }

        IllegalStateException sameTransactionFailure() {
            return sameTransactionFailure.get();
        }

        IllegalStateException requiresNewFailure() {
            return requiresNewFailure.get();
        }

        CompletionException crossThreadFailure() {
            return crossThreadFailure.get();
        }

        void reset() {
            mode.set(null);
            captured.set(null);
            sameTransactionFailure.set(null);
            requiresNewFailure.set(null);
            crossThreadFailure.set(null);
        }
    }
}
