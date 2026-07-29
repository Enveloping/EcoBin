package org.enveloping.ecobin.funds.application.startdelivery;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;

final class StartDeliveryWalletPolicy {

    private StartDeliveryWalletPolicy() {
    }

    static void requireEligible(
            long availableBalanceCent,
            String deliveryGateState,
            long openBalanceFloorCent) {
        if (!isEligible(
                availableBalanceCent,
                deliveryGateState,
                openBalanceFloorCent)) {
            throw new TargetApiException(
                    422,
                    "WALLET.DELIVERY_LIMIT_REACHED",
                    "钱包当前不具备开始新投递的资格");
        }
    }

    static boolean isEligible(
            long availableBalanceCent,
            String deliveryGateState,
            long openBalanceFloorCent) {
        return "OPEN".equals(deliveryGateState)
                && availableBalanceCent > openBalanceFloorCent;
    }
}
