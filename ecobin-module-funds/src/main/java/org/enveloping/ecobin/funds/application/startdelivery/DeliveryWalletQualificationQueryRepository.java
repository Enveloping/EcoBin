package org.enveloping.ecobin.funds.application.startdelivery;

import java.util.Optional;

interface DeliveryWalletQualificationQueryRepository {

    Optional<WalletQualificationRow> findCurrentWallet(
            long tenantId,
            long organizationId,
            long organizationUserId);

    record WalletQualificationRow(
            long availableBalanceCent,
            String deliveryGateState) {
    }
}
