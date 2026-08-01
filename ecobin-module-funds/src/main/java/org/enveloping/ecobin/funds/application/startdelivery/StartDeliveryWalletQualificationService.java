package org.enveloping.ecobin.funds.application.startdelivery;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.command.StartDeliveryWalletQualificationCommand;
import org.enveloping.ecobin.funds.api.id.WalletUid;
import org.enveloping.ecobin.funds.api.port.StartDeliveryWalletQualificationPort;
import org.enveloping.ecobin.funds.api.result.QualifiedDeliveryWallet;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Objects;

@Service
public class StartDeliveryWalletQualificationService
        implements StartDeliveryWalletQualificationPort {

    private final StartDeliveryWalletLockRepository repository;

    StartDeliveryWalletQualificationService(
            StartDeliveryWalletLockRepository repository) {
        this.repository = repository;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public QualifiedDeliveryWallet lockAndRequireEligible(
            StartDeliveryWalletQualificationCommand command) {
        requireActiveTransaction();
        Objects.requireNonNull(command, "command");
        return command.walletOwnerRef().withWalletOwnerOnce(
                (tenantId, organizationId, organizationUserId) -> {
                    StartDeliveryWalletLockRepository.WalletRow wallet =
                            repository.lockWallet(
                                            tenantId,
                                            organizationId,
                                            organizationUserId)
                                    .orElseThrow(
                                            StartDeliveryWalletQualificationService
                                                    ::walletMissing);
                    StartDeliveryWalletPolicy.requireEligible(
                            wallet.availableBalanceCent(),
                            wallet.deliveryGateState(),
                            command.openBalanceFloorCent());
                    return new QualifiedDeliveryWallet(
                            new WalletUid(wallet.walletUid()),
                            wallet.availableBalanceCent(),
                            command.openBalanceFloorCent());
                });
    }

    private static void requireActiveTransaction() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "start-delivery wallet qualification "
                            + "requires an existing transaction");
        }
    }

    private static TargetApiException walletMissing() {
        return new TargetApiException(
                500,
                "WALLET.NOT_INITIALIZED",
                "机构用户钱包未初始化，无法开始投递");
    }
}
