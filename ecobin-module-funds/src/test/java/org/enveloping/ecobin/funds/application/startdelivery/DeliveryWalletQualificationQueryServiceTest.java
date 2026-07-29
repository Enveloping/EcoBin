package org.enveloping.ecobin.funds.application.startdelivery;

import org.enveloping.ecobin.funds.api.command.DeliveryWalletQualificationQuery;
import org.enveloping.ecobin.funds.api.result.DeliveryWalletQualificationBlocker;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class DeliveryWalletQualificationQueryServiceTest {

    @BeforeEach
    void beginReadOnlyTransaction() {
        TransactionSynchronizationManager.setActualTransactionActive(true);
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(true);
    }

    @AfterEach
    void endReadOnlyTransaction() {
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
        TransactionSynchronizationManager
                .setActualTransactionActive(false);
    }

    @Test
    void returnsEligibleSnapshotWithoutTakingAWriteLock() {
        UUID userUid = UUID.randomUUID();
        FakeRepository repository = new FakeRepository(
                Optional.of(
                        new DeliveryWalletQualificationQueryRepository
                                .WalletQualificationRow(-99, "OPEN")));
        DeliveryWalletQualificationQueryService service =
                new DeliveryWalletQualificationQueryService(repository);

        var result = service.current(new DeliveryWalletQualificationQuery(
                new OrganizationUserUid(userUid),
                -100,
                walletQueryReference(11, 22, 33, userUid)));

        assertTrue(result.eligible());
        assertEquals(0, result.blockers().size());
        assertEquals(-99, result.availableBalanceCent());
        assertEquals(11, repository.tenantId);
        assertEquals(22, repository.organizationId);
        assertEquals(33, repository.organizationUserId);
    }

    @Test
    void returnsStableBlockerWhenBalanceEqualsFloor() {
        UUID userUid = UUID.randomUUID();
        DeliveryWalletQualificationQueryService service =
                new DeliveryWalletQualificationQueryService(
                        new FakeRepository(Optional.of(
                                new DeliveryWalletQualificationQueryRepository
                                        .WalletQualificationRow(
                                                -100,
                                                "OPEN"))));

        var result = service.current(new DeliveryWalletQualificationQuery(
                new OrganizationUserUid(userUid),
                -100,
                walletQueryReference(11, 22, 33, userUid)));

        assertFalse(result.eligible());
        assertEquals(
                DeliveryWalletQualificationBlocker
                        .WALLET_DELIVERY_LIMIT_REACHED,
                result.blockers().getFirst());
    }

    @Test
    void refusesAUidThatDoesNotMatchTheIdentityIssuedReference() {
        UUID issuedUserUid = UUID.randomUUID();
        UUID suppliedUserUid = UUID.randomUUID();
        DeliveryWalletQualificationQueryService service =
                new DeliveryWalletQualificationQueryService(
                        new FakeRepository(Optional.of(
                                new DeliveryWalletQualificationQueryRepository
                                        .WalletQualificationRow(0, "OPEN"))));

        assertThrows(
                IllegalArgumentException.class,
                () -> service.current(
                        new DeliveryWalletQualificationQuery(
                                new OrganizationUserUid(suppliedUserUid),
                                -100,
                                walletQueryReference(
                                        11,
                                        22,
                                        33,
                                        issuedUserUid))));
    }

    @Test
    void queryRequiresAnExistingReadOnlyOuterTransaction()
            throws NoSuchMethodException {
        Transactional annotation =
                DeliveryWalletQualificationQueryService.class
                        .getMethod(
                                "current",
                                DeliveryWalletQualificationQuery.class)
                        .getAnnotation(Transactional.class);

        assertEquals(
                Propagation.MANDATORY,
                annotation.propagation());
        assertTrue(annotation.readOnly());
    }

    @SuppressWarnings("unchecked")
    private static DeliveryWalletQueryOwnerRef walletQueryReference(
            long tenantId,
            long organizationId,
            long organizationUserId,
            UUID organizationUserUid) {
        DeliveryWalletQueryOwnerRef reference =
                mock(DeliveryWalletQueryOwnerRef.class);
        when(reference.withWalletQualificationOwnerOnce(any()))
                .thenAnswer(invocation -> {
                    DeliveryWalletQueryOwnerRef
                            .WalletQualificationOwnerFunction<Object>
                            function = invocation.getArgument(0);
                    return function.apply(
                            tenantId,
                            organizationId,
                            organizationUserId,
                            organizationUserUid);
                });
        return reference;
    }

    private static final class FakeRepository
            implements DeliveryWalletQualificationQueryRepository {

        private final Optional<WalletQualificationRow> result;
        private long tenantId;
        private long organizationId;
        private long organizationUserId;

        private FakeRepository(Optional<WalletQualificationRow> result) {
            this.result = result;
        }

        @Override
        public Optional<WalletQualificationRow> findCurrentWallet(
                long tenantId,
                long organizationId,
                long organizationUserId) {
            this.tenantId = tenantId;
            this.organizationId = organizationId;
            this.organizationUserId = organizationUserId;
            return result;
        }
    }
}
