package org.enveloping.ecobin.funds.application.startdelivery;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.command.DeliveryWalletQualificationQuery;
import org.enveloping.ecobin.funds.api.port.DeliveryWalletQualificationQueryPort;
import org.enveloping.ecobin.funds.api.result.DeliveryWalletQualification;
import org.enveloping.ecobin.funds.api.result.DeliveryWalletQualificationBlocker;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;
import java.util.Objects;

@Service
public class DeliveryWalletQualificationQueryService
        implements DeliveryWalletQualificationQueryPort {

    private final DeliveryWalletQualificationQueryRepository repository;

    DeliveryWalletQualificationQueryService(
            DeliveryWalletQualificationQueryRepository repository) {
        this.repository = repository;
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public DeliveryWalletQualification current(
            DeliveryWalletQualificationQuery query) {
        requireReadOnlyTransaction();
        Objects.requireNonNull(query, "query");
        return query.walletQueryOwnerRef()
                .withWalletQualificationOwnerOnce(
                        (tenantId,
                                organizationId,
                                organizationUserId,
                                organizationUserUid) -> {
                            if (!query.organizationUserUid()
                                    .value()
                                    .equals(organizationUserUid)) {
                                throw new IllegalArgumentException(
                                        "organizationUserUid does not "
                                                + "match wallet query reference");
                            }
                            DeliveryWalletQualificationQueryRepository
                                    .WalletQualificationRow wallet =
                                    repository.findCurrentWallet(
                                                    tenantId,
                                                    organizationId,
                                                    organizationUserId)
                                            .orElseThrow(
                                                    DeliveryWalletQualificationQueryService
                                                            ::walletMissing);
                            boolean eligible =
                                    StartDeliveryWalletPolicy.isEligible(
                                            wallet.availableBalanceCent(),
                                            wallet.deliveryGateState(),
                                            query.openBalanceFloorCent());
                            List<DeliveryWalletQualificationBlocker>
                                    blockers = eligible
                                    ? List.of()
                                    : List.of(
                                            DeliveryWalletQualificationBlocker
                                                    .WALLET_DELIVERY_LIMIT_REACHED);
                            return new DeliveryWalletQualification(
                                    query.organizationUserUid(),
                                    wallet.availableBalanceCent(),
                                    query.openBalanceFloorCent(),
                                    eligible,
                                    blockers);
                        });
    }

    private static void requireReadOnlyTransaction() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()) {
            throw new IllegalStateException(
                    "delivery wallet qualification query requires "
                            + "an existing read-only transaction");
        }
    }

    private static TargetApiException walletMissing() {
        return new TargetApiException(
                500,
                "WALLET.NOT_INITIALIZED",
                "机构用户钱包未初始化，无法查询投递资格");
    }
}
