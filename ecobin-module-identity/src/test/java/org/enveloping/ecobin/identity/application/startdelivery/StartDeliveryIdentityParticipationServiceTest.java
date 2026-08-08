package org.enveloping.ecobin.identity.application.startdelivery;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.DeliverySessionOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryAuditActorRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryOrganizationScopeRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryWalletOwnerRef;
import org.enveloping.ecobin.identity.api.result.LockedMiniappDeliveryScope;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActor;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActorContext;
import org.enveloping.ecobin.identity.application.persistence.StartDeliveryPersistenceRefFactory;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;

class StartDeliveryIdentityParticipationServiceTest {

    @Test
    void locksScopeAndUserInTwoSeparateOrderedPhases() {
        TargetMiniappActor actor = actor("USER", true);
        FakeRepository repository = new FakeRepository(actor);
        FakeReferenceFactory references = new FakeReferenceFactory();
        StartDeliveryIdentityParticipationService service =
                new StartDeliveryIdentityParticipationService(
                        repository,
                        references);

        try (TransactionFixture ignored = beginTransaction(actor)) {
            LockedMiniappDeliveryScope scope =
                    service.lockCurrentMiniappScope();

            assertEquals(
                    List.of("tenant", "organization", "miniapp"),
                    repository.calls);
            repository.calls.add("recycling-config-head");

            var user = service.lockCurrentOrganizationUser(scope);

            assertEquals(
                    List.of(
                            "tenant",
                            "organization",
                            "miniapp",
                            "recycling-config-head",
                            "organization-user",
                            "organization-user-session"),
                    repository.calls);
            assertEquals(actor.principalUid(),
                    user.organizationUserUid().value());
            assertEquals(actor.sessionUid(),
                    user.loginSessionUid().value());
            assertSame(references.scopeRef, scope.organizationScopeRef());
            assertSame(references.walletRef, user.walletOwnerRef());
            assertSame(
                    references.sessionUserRef,
                    user.deliverySessionUserRef());
            assertSame(
                    references.auditActorRef,
                    user.auditActorRef());
        }
    }

    @Test
    void secondPhaseRequiresTheExactFirstPhaseResult() {
        TargetMiniappActor actor = actor("USER", true);
        FakeRepository repository = new FakeRepository(actor);
        FakeReferenceFactory references = new FakeReferenceFactory();
        StartDeliveryIdentityParticipationService service =
                new StartDeliveryIdentityParticipationService(
                        repository,
                        references);

        try (TransactionFixture ignored = beginTransaction(actor)) {
            LockedMiniappDeliveryScope issued =
                    service.lockCurrentMiniappScope();
            LockedMiniappDeliveryScope reconstructed =
                    new LockedMiniappDeliveryScope(
                            issued.tenantCode(),
                            issued.organizationCode(),
                            issued.miniappAppId(),
                            issued.loginSessionUid(),
                            issued.organizationScopeRef());

            assertThrows(
                    IllegalStateException.class,
                    () -> service.lockCurrentOrganizationUser(
                            reconstructed));
            service.lockCurrentOrganizationUser(issued);
            assertThrows(
                    IllegalStateException.class,
                    () -> service.lockCurrentOrganizationUser(issued));
        }
    }

    @Test
    void rejectsUserWithoutBoundPhoneAfterSessionWasLocked() {
        TargetMiniappActor actor = actor("USER", false);
        FakeRepository repository = new FakeRepository(actor);
        FakeReferenceFactory references = new FakeReferenceFactory();
        StartDeliveryIdentityParticipationService service =
                new StartDeliveryIdentityParticipationService(
                        repository,
                        references);

        try (TransactionFixture ignored = beginTransaction(actor)) {
            LockedMiniappDeliveryScope scope =
                    service.lockCurrentMiniappScope();

            TargetApiException failure = assertThrows(
                    TargetApiException.class,
                    () -> service.lockCurrentOrganizationUser(scope));

            assertEquals(422, failure.status());
            assertEquals(
                    "IDENTITY.PHONE_BINDING_REQUIRED",
                    failure.code());
            assertEquals(
                    "organization-user-session",
                    repository.calls.getLast());
        }
    }

    @Test
    void rejectsCleaningEntryBeforeTakingAnyLock() {
        TargetMiniappActor actor = actor("CLEANING", true);
        FakeRepository repository = new FakeRepository(actor);
        StartDeliveryIdentityParticipationService service =
                new StartDeliveryIdentityParticipationService(
                        repository,
                        new FakeReferenceFactory());

        try (TransactionFixture ignored = beginTransaction(actor)) {
            TargetApiException failure = assertThrows(
                    TargetApiException.class,
                    service::lockCurrentMiniappScope);

            assertEquals(401, failure.status());
            assertEquals(
                    "AUTH.TOKEN_AUDIENCE_MISMATCH",
                    failure.code());
            assertEquals(List.of(), repository.calls);
        }
    }

    @Test
    void bothLockMethodsRequireAnExistingOuterTransaction()
            throws NoSuchMethodException {
        Transactional scopeAnnotation =
                StartDeliveryIdentityParticipationService.class
                        .getMethod("lockCurrentMiniappScope")
                        .getAnnotation(Transactional.class);
        Transactional userAnnotation =
                StartDeliveryIdentityParticipationService.class
                        .getMethod(
                                "lockCurrentOrganizationUser",
                                LockedMiniappDeliveryScope.class)
                        .getAnnotation(Transactional.class);

        assertEquals(
                Propagation.MANDATORY,
                scopeAnnotation.propagation());
        assertEquals(
                Propagation.MANDATORY,
                userAnnotation.propagation());
    }

    private static TransactionFixture beginTransaction(
            TargetMiniappActor actor) {
        Object resourceKey = new Object();
        TransactionSynchronizationManager.initSynchronization();
        TransactionSynchronizationManager.setActualTransactionActive(true);
        TransactionSynchronizationManager.bindResource(
                resourceKey,
                new Object());
        TargetMiniappActorContext.set(actor);
        return new TransactionFixture(resourceKey);
    }

    private static TargetMiniappActor actor(
            String entryMode,
            boolean phoneBound) {
        UUID userUid = UUID.randomUUID();
        UUID sessionUid = UUID.randomUUID();
        Instant expiresAt = Instant.now().plusSeconds(3600);
        return new TargetMiniappActor(
                TrustedPrincipalKind.ORGANIZATION_USER,
                TrustedAudience.MINIAPP,
                44,
                userUid,
                11,
                "pilot",
                22,
                "station",
                "试点回收站",
                33,
                "wx-pilot-app",
                40,
                UUID.randomUUID(),
                44,
                userUid,
                null,
                sessionUid,
                5,
                expiresAt,
                entryMode,
                "测试用户",
                List.of(),
                phoneBound ? "+8613800138000" : null,
                phoneBound ? Instant.now().minusSeconds(60) : null);
    }

    private record TransactionFixture(Object resourceKey)
            implements AutoCloseable {

        @Override
        public void close() {
            TargetMiniappActorContext.clear();
            if (TransactionSynchronizationManager
                    .isSynchronizationActive()) {
                TransactionSynchronizationManager.clearSynchronization();
            }
            if (TransactionSynchronizationManager.hasResource(resourceKey)) {
                TransactionSynchronizationManager.unbindResource(
                        resourceKey);
            }
            TransactionSynchronizationManager
                    .setActualTransactionActive(false);
        }
    }

    private static final class FakeRepository
            implements StartDeliveryIdentityLockRepository {

        private final List<String> calls = new ArrayList<>();
        private final TargetMiniappActor actor;

        private FakeRepository(TargetMiniappActor actor) {
            this.actor = actor;
        }

        @Override
        public Optional<TenantRow> lockTenant(long tenantId) {
            calls.add("tenant");
            return Optional.of(new TenantRow(
                    actor.tenantId(),
                    actor.tenantCode(),
                    "ENABLED"));
        }

        @Override
        public Optional<OrganizationRow> lockOrganization(
                long organizationId) {
            calls.add("organization");
            return Optional.of(new OrganizationRow(
                    actor.organizationId(),
                    actor.tenantId(),
                    actor.organizationCode(),
                    "ENABLED"));
        }

        @Override
        public Optional<MiniappRow> lockMiniapp(
                long tenantId,
                long organizationId,
                long miniappId) {
            calls.add("miniapp");
            return Optional.of(new MiniappRow(
                    actor.miniappChannelId(),
                    actor.tenantId(),
                    actor.organizationId(),
                    actor.appId(),
                    true,
                    Instant.now().minusSeconds(3600)));
        }

        @Override
        public Optional<OrganizationUserRow> lockOrganizationUser(
                long organizationUserId) {
            calls.add("organization-user");
            return Optional.of(new OrganizationUserRow(
                    actor.organizationUserId(),
                    actor.principalUid(),
                    actor.tenantId(),
                    actor.organizationId(),
                    actor.miniappChannelId(),
                    actor.phoneE164(),
                    actor.phoneBoundAt(),
                    "ACTIVE",
                    actor.authVersion()));
        }

        @Override
        public Optional<OrganizationUserSessionRow>
                lockOrganizationUserSession(UUID sessionUid) {
            calls.add("organization-user-session");
            return Optional.of(new OrganizationUserSessionRow(
                    actor.sessionUid(),
                    actor.tenantId(),
                    actor.organizationId(),
                    actor.miniappChannelId(),
                    actor.organizationUserId(),
                    Instant.now().minusSeconds(300),
                    actor.expiresAt(),
                    null,
                    actor.authVersion()));
        }
    }

    private static final class FakeReferenceFactory
            implements StartDeliveryPersistenceRefFactory {

        private final StartDeliveryOrganizationScopeRef scopeRef =
                mock(StartDeliveryOrganizationScopeRef.class);
        private final StartDeliveryWalletOwnerRef walletRef =
                mock(StartDeliveryWalletOwnerRef.class);
        private final DeliverySessionOrganizationUserRef sessionUserRef =
                mock(DeliverySessionOrganizationUserRef.class);
        private final StartDeliveryAuditActorRef auditActorRef =
                mock(StartDeliveryAuditActorRef.class);

        @Override
        public StartDeliveryOrganizationScopeRef issueOrganizationScope(
                long tenantKey,
                long organizationKey) {
            return scopeRef;
        }

        @Override
        public StartDeliveryWalletOwnerRef issueWalletOwner(
                long tenantKey,
                long organizationKey,
                long organizationUserKey) {
            return walletRef;
        }

        @Override
        public DeliverySessionOrganizationUserRef issueDeliverySessionUser(
                long tenantKey,
                long organizationKey,
                long organizationUserKey) {
            return sessionUserRef;
        }

        @Override
        public StartDeliveryAuditActorRef issueAuditActor(
                long tenantKey,
                long organizationKey,
                long organizationUserKey) {
            return auditActorRef;
        }
    }
}
