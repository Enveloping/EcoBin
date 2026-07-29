package org.enveloping.ecobin.funds.application.startdelivery;

import java.util.Optional;
import java.util.UUID;

interface StartDeliveryWalletLockRepository {

    Optional<WalletRow> lockWallet(
            long tenantId,
            long organizationId,
            long organizationUserId);

    record WalletRow(
            UUID walletUid,
            long availableBalanceCent,
            String deliveryGateState) {
    }
}
