package org.enveloping.ecobin.funds.api.port;

import java.time.Instant;
import java.util.Objects;

/** 商家转账渠道边界。所有续办和查单必须复用固定 outBillNo。 */
public interface MerchantTransferChannelPort {

    MerchantTransferResult submit(MerchantTransferRequest request);

    MerchantTransferResult query(MerchantTransferQuery query);

    MerchantTransferResult cancel(MerchantTransferQuery query);

    record MerchantTransferRequest(
            String mchid,
            String appid,
            String outBillNo,
            String openid,
            long amountCent,
            String sceneId,
            String reportType,
            String reportContent,
            String remark,
            String transferPageStyle,
            String notifyUrl) {

        public MerchantTransferRequest {
            requireText(mchid, "mchid");
            requireText(appid, "appid");
            requireText(outBillNo, "outBillNo");
            requireText(openid, "openid");
            if (amountCent <= 0) {
                throw new IllegalArgumentException("amountCent must be positive");
            }
            requireText(sceneId, "sceneId");
            requireText(reportType, "reportType");
            requireText(reportContent, "reportContent");
            requireText(remark, "remark");
            requireText(transferPageStyle, "transferPageStyle");
            requireText(notifyUrl, "notifyUrl");
        }
    }

    record MerchantTransferQuery(String mchid, String outBillNo) {

        public MerchantTransferQuery {
            requireText(mchid, "mchid");
            requireText(outBillNo, "outBillNo");
        }
    }

    record MerchantTransferResult(
            Outcome outcome,
            String channelState,
            String transferBillNo,
            String packageInfo,
            String errorCode,
            String failReason,
            String diagnostic,
            Instant channelTime) {

        public MerchantTransferResult {
            Objects.requireNonNull(outcome, "outcome");
        }

        public enum Outcome {
            PROCESSING,
            WAIT_USER_CONFIRM,
            SUCCESS,
            FAIL,
            CANCELLED,
            NOT_ENOUGH,
            RETRYABLE_FAILURE,
            UNKNOWN
        }
    }

    private static void requireText(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
    }
}
