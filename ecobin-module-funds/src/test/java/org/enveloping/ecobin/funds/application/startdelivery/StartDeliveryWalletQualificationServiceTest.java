package org.enveloping.ecobin.funds.application.startdelivery;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.command.StartDeliveryWalletQualificationCommand;
import org.enveloping.ecobin.funds.api.result.QualifiedDeliveryWallet;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryWalletOwnerRef;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class StartDeliveryWalletQualificationServiceTest {

    @BeforeEach
    void beginTransaction() {
        TransactionSynchronizationManager.setActualTransactionActive(true);
    }

    @AfterEach
    void endTransaction() {
        TransactionSynchronizationManager
                .setActualTransactionActive(false);
    }

    @Test
    void locksWalletThroughTheRelationReferenceAndReturnsSnapshot() {
        UUID walletUid = UUID.randomUUID();
        FakeRepository repository = new FakeRepository(
                Optional.of(new StartDeliveryWalletLockRepository.WalletRow(
                        walletUid,
                        -99,
                        "OPEN")));
        StartDeliveryWalletQualificationService service =
                new StartDeliveryWalletQualificationService(repository);

        QualifiedDeliveryWallet result =
                service.lockAndRequireEligible(new StartDeliveryWalletQualificationCommand(
                        walletReference(11, 22, 33),
                        -100));

        assertEquals(11, repository.tenantId);
        assertEquals(22, repository.organizationId);
        assertEquals(33, repository.organizationUserId);
        assertEquals(walletUid, result.walletUid().value());
        assertEquals(-99, result.availableBalanceCent());
        assertEquals(-100, result.openBalanceFloorCent());
    }

    @Test
    void missingWalletIsAnInitializationInvariantFailure() {
        StartDeliveryWalletQualificationService service =
                new StartDeliveryWalletQualificationService(
                        new FakeRepository(Optional.empty()));

        TargetApiException failure = assertThrows(
                TargetApiException.class,
                () -> service.lockAndRequireEligible(
                        new StartDeliveryWalletQualificationCommand(
                                walletReference(11, 22, 33),
                                -100)));

        assertEquals(500, failure.status());
        assertEquals("WALLET.NOT_INITIALIZED", failure.code());
    }

    @Test
    void methodRequiresAnExistingOuterTransaction()
            throws NoSuchMethodException {
        Transactional annotation =
                StartDeliveryWalletQualificationService.class
                        .getMethod(
                                "lockAndRequireEligible",
                                StartDeliveryWalletQualificationCommand.class)
                        .getAnnotation(Transactional.class);

        assertEquals(
                Propagation.MANDATORY,
                annotation.propagation());
    }

    @SuppressWarnings("unchecked")
    private static StartDeliveryWalletOwnerRef walletReference(
            long tenantId,
            long organizationId,
            long organizationUserId) {
        StartDeliveryWalletOwnerRef reference =
                mock(StartDeliveryWalletOwnerRef.class);
        when(reference.withWalletOwnerOnce(any())).thenAnswer(invocation -> {
            StartDeliveryWalletOwnerRef.WalletOwnerFunction<Object>
                    function = invocation.getArgument(0);
            return function.apply(
                    tenantId,
                    organizationId,
                    organizationUserId);
        });
        return reference;
    }

    private static final class FakeRepository
            implements StartDeliveryWalletLockRepository {

        private final Optional<WalletRow> result;
        private long tenantId;
        private long organizationId;
        private long organizationUserId;

        private FakeRepository(Optional<WalletRow> result) {
            this.result = result;
        }

        @Override
        public Optional<WalletRow> lockWallet(
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
