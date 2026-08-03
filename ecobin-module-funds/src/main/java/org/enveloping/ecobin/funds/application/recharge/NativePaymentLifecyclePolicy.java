package org.enveloping.ecobin.funds.application.recharge;

import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort.NativePaymentResult;

import java.time.LocalDateTime;

final class NativePaymentLifecyclePolicy {

    private NativePaymentLifecyclePolicy() {
    }

    static boolean successAlreadyEstablished(
            String businessState,
            String channelState,
            String transactionId) {
        return transactionId != null
                || "SUCCESS".equals(channelState)
                || "SIMULATED_SUCCESS".equals(channelState)
                || "PAID_PENDING_POST".equals(businessState)
                || "POSTED".equals(businessState);
    }

    static boolean shouldCloseAfterExpiredUnpaidQuery(
            LocalDateTime expiresAt,
            LocalDateTime observedAt,
            NativePaymentResult result) {
        if (expiresAt == null || expiresAt.isAfter(observedAt)
                || result.outcome() != NativePaymentResult.Outcome.ACCEPTED) {
            return false;
        }
        return "NOTPAY".equals(result.channelState())
                || "SIMULATED_NOTPAY".equals(result.channelState());
    }

    static String closedBusinessState(
            LocalDateTime expiresAt,
            LocalDateTime observedAt) {
        return expiresAt != null && !expiresAt.isAfter(observedAt)
                ? "EXPIRED"
                : "CLOSED";
    }
}
