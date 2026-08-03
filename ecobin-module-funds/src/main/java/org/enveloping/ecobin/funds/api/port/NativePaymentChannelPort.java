package org.enveloping.ecobin.funds.api.port;

import java.time.Instant;
import java.util.Objects;

/**
 * Native 支付渠道边界。实现必须按 outTradeNo 幂等，不能自行换单号。
 */
public interface NativePaymentChannelPort {

    NativePaymentResult create(NativePaymentRequest request);

    NativePaymentResult query(NativePaymentQuery query);

    NativePaymentResult close(NativePaymentQuery query);

    record NativePaymentRequest(
            String mchid,
            String appid,
            String outTradeNo,
            long amountCent,
            String description,
            Instant expiresAt,
            String notifyUrl) {

        public NativePaymentRequest {
            requireText(mchid, "mchid");
            requireText(appid, "appid");
            requireText(outTradeNo, "outTradeNo");
            if (amountCent <= 0) {
                throw new IllegalArgumentException("amountCent must be positive");
            }
            requireText(description, "description");
            Objects.requireNonNull(expiresAt, "expiresAt");
            requireText(notifyUrl, "notifyUrl");
        }
    }

    record NativePaymentQuery(String mchid, String outTradeNo) {

        public NativePaymentQuery {
            requireText(mchid, "mchid");
            requireText(outTradeNo, "outTradeNo");
        }
    }

    record NativePaymentResult(
            Outcome outcome,
            String channelState,
            String codeUrl,
            String transactionId,
            String errorCode,
            String diagnostic,
            Instant channelTime,
            String mchid,
            String appid,
            String outTradeNo,
            Long totalAmountCent,
            String currency) {

        public NativePaymentResult {
            Objects.requireNonNull(outcome, "outcome");
        }

        public NativePaymentResult(
                Outcome outcome,
                String channelState,
                String codeUrl,
                String transactionId,
                String errorCode,
                String diagnostic,
                Instant channelTime) {
            this(outcome, channelState, codeUrl, transactionId, errorCode,
                    diagnostic, channelTime, null, null, null, null, null);
        }

        public enum Outcome {
            ACCEPTED,
            SUCCEEDED,
            REFUNDED,
            CLOSED,
            ORDER_ALREADY_EXISTS,
            NOT_FOUND,
            RETRYABLE_FAILURE,
            PERMANENT_FAILURE,
            UNKNOWN_STATE
        }
    }

    private static void requireText(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
    }
}
