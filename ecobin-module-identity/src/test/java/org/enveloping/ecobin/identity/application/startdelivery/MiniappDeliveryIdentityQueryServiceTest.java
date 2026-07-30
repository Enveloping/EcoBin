package org.enveloping.ecobin.identity.application.startdelivery;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;
import org.enveloping.ecobin.identity.api.persistence.WalletQueryScopeRef;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActor;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActorContext;
import org.enveloping.ecobin.identity.application.persistence.DeliveryIdentityQueryRefFactory;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;

class MiniappDeliveryIdentityQueryServiceTest {

    private final FakeReferenceFactory referenceFactory =
            new FakeReferenceFactory();
    private final MiniappDeliveryIdentityQueryService service =
            new MiniappDeliveryIdentityQueryService(referenceFactory);
    private Object resourceKey;

    @BeforeEach
    void beginReadOnlyTransaction() {
        resourceKey = new Object();
        TransactionSynchronizationManager.initSynchronization();
        TransactionSynchronizationManager.setActualTransactionActive(true);
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(true);
        TransactionSynchronizationManager.bindResource(
                resourceKey,
                new Object());
    }

    @AfterEach
    void clearActor() {
        TargetMiniappActorContext.clear();
        if (TransactionSynchronizationManager
                .isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
        if (TransactionSynchronizationManager.hasResource(resourceKey)) {
            TransactionSynchronizationManager.unbindResource(resourceKey);
        }
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
        TransactionSynchronizationManager
                .setActualTransactionActive(false);
    }

    @Test
    void returnsOnlyPublicCurrentUserSnapshotWithoutDatabaseAccess() {
        TargetMiniappActor actor = actor("USER", false);
        TargetMiniappActorContext.set(actor);

        var result = service.current();

        assertEquals(actor.tenantCode(), result.tenantCode());
        assertEquals(
                actor.organizationCode(),
                result.organizationCode());
        assertEquals(
                actor.principalUid(),
                result.organizationUserUid().value());
        assertFalse(result.phoneBound());
        assertEquals(actor.sessionUid(), result.sessionUid().value());
        assertSame(
                referenceFactory.deliveryQueryUserRef,
                result.deliveryQueryUserRef());
        assertSame(
                referenceFactory.walletQueryOwnerRef,
                result.walletQueryOwnerRef());
    }

    @Test
    void rejectsNonUserMiniappEntry() {
        TargetMiniappActorContext.set(actor("CLEANING", true));

        TargetApiException failure = assertThrows(
                TargetApiException.class,
                service::current);

        assertEquals(401, failure.status());
        assertEquals(
                "AUTH.TOKEN_AUDIENCE_MISMATCH",
                failure.code());
    }

    @Test
    void currentRequiresAnExistingReadOnlyTransaction()
            throws NoSuchMethodException {
        Transactional annotation =
                MiniappDeliveryIdentityQueryService.class
                        .getMethod("current")
                        .getAnnotation(Transactional.class);

        assertEquals(
                Propagation.MANDATORY,
                annotation.propagation());
        assertEquals(true, annotation.readOnly());
    }

    private static TargetMiniappActor actor(
            String entryMode,
            boolean phoneBound) {
        return new TargetMiniappActor(
                TrustedPrincipalKind.ORGANIZATION_USER,
                TrustedAudience.MINIAPP,
                44,
                UUID.randomUUID(),
                11,
                "pilot",
                22,
                "station",
                "试点回收站",
                33,
                "wx-pilot-app",
                "local:pilot-secret",
                44,
                null,
                UUID.randomUUID(),
                5,
                Instant.now().plusSeconds(3600),
                entryMode,
                "测试用户",
                List.of(),
                phoneBound ? "+8613800138000" : null,
                phoneBound ? Instant.now() : null);
    }

    private static final class FakeReferenceFactory
            implements DeliveryIdentityQueryRefFactory {

        private final DeliveryQueryOrganizationUserRef
                deliveryQueryUserRef =
                mock(DeliveryQueryOrganizationUserRef.class);
        private final DeliveryWalletQueryOwnerRef walletQueryOwnerRef =
                mock(DeliveryWalletQueryOwnerRef.class);

        @Override
        public DeliveryQueryOrganizationUserRef issueDeliveryQueryUser(
                long tenantKey,
                long organizationKey,
                long organizationUserKey,
                UUID organizationUserUid) {
            return deliveryQueryUserRef;
        }

        @Override
        public DeliveryWalletQueryOwnerRef issueWalletQueryOwner(
                long tenantKey,
                long organizationKey,
                long organizationUserKey,
                UUID organizationUserUid) {
            return walletQueryOwnerRef;
        }

        @Override
        public WalletQueryScopeRef issueWalletScope(
                long tenantKey,
                long organizationKey,
                Long organizationUserKey,
                UUID organizationUserUid) {
            return mock(WalletQueryScopeRef.class);
        }
    }
}
