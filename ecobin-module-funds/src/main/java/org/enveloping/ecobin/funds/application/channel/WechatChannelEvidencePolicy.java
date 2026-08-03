package org.enveloping.ecobin.funds.application.channel;

import org.enveloping.ecobin.funds.api.port.MerchantTransferChannelPort.MerchantTransferResult;
import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort.NativePaymentResult;

import java.util.ArrayList;
import java.util.List;

/** 微信查单证据只有与本地固定请求逐项一致后，才能推进资金状态。 */
public final class WechatChannelEvidencePolicy {

    private WechatChannelEvidencePolicy() {
    }

    public static Validation validateNativeQuery(
            String expectedMchid,
            String expectedAppid,
            String expectedOutTradeNo,
            long expectedAmountCent,
            NativePaymentResult actual) {
        List<String> violations = new ArrayList<>();
        requireEqual(actual.mchid(), expectedMchid, "MCHID", violations);
        requireEqual(actual.appid(), expectedAppid, "APPID", violations);
        requireEqual(actual.outTradeNo(), expectedOutTradeNo,
                "OUT_TRADE_NO", violations);
        if (actual.outcome() == NativePaymentResult.Outcome.SUCCEEDED
                || actual.outcome()
                == NativePaymentResult.Outcome.REFUNDED) {
            if (actual.totalAmountCent() == null) {
                violations.add("AMOUNT_MISSING");
            } else if (actual.totalAmountCent() != expectedAmountCent) {
                violations.add("AMOUNT_MISMATCH");
            }
            requireEqual(actual.currency(), "CNY", "CURRENCY", violations);
        } else if (actual.totalAmountCent() != null
                && actual.totalAmountCent() != expectedAmountCent) {
            violations.add("AMOUNT_MISMATCH");
        }
        if (actual.outcome() != NativePaymentResult.Outcome.SUCCEEDED
                && actual.outcome()
                != NativePaymentResult.Outcome.REFUNDED
                && actual.currency() != null
                && !"CNY".equals(actual.currency())) {
            violations.add("CURRENCY_MISMATCH");
        }
        return new Validation(violations);
    }

    public static Validation validateTransferQuery(
            String expectedMchid,
            String expectedAppid,
            String expectedOutBillNo,
            String expectedTransferBillNo,
            long expectedAmountCent,
            String expectedOpenid,
            MerchantTransferResult actual) {
        List<String> violations = new ArrayList<>();
        requireEqual(actual.mchid(), expectedMchid, "MCHID", violations);
        requireEqual(actual.appid(), expectedAppid, "APPID", violations);
        requireEqual(actual.outBillNo(), expectedOutBillNo,
                "OUT_BILL_NO", violations);
        if (actual.transferBillNo() == null
                || actual.transferBillNo().isBlank()) {
            violations.add("TRANSFER_BILL_NO_MISSING");
        } else if (expectedTransferBillNo != null
                && !expectedTransferBillNo.equals(actual.transferBillNo())) {
            violations.add("TRANSFER_BILL_NO_MISMATCH");
        }
        if (actual.transferAmountCent() == null) {
            violations.add("AMOUNT_MISSING");
        } else if (actual.transferAmountCent() != expectedAmountCent) {
            violations.add("AMOUNT_MISMATCH");
        }
        requireEqual(actual.openid(), expectedOpenid,
                "OPENID", violations);
        return new Validation(violations);
    }

    public static Validation validateTransferResponseFields(
            String expectedMchid,
            String expectedAppid,
            String expectedOutBillNo,
            String expectedTransferBillNo,
            long expectedAmountCent,
            String expectedOpenid,
            MerchantTransferResult actual) {
        List<String> violations = new ArrayList<>();
        compareWhenPresent(actual.mchid(), expectedMchid, "MCHID", violations);
        compareWhenPresent(actual.appid(), expectedAppid, "APPID", violations);
        compareWhenPresent(actual.outBillNo(), expectedOutBillNo,
                "OUT_BILL_NO", violations);
        if (expectedTransferBillNo != null) {
            compareWhenPresent(actual.transferBillNo(), expectedTransferBillNo,
                    "TRANSFER_BILL_NO", violations);
        }
        if (actual.transferAmountCent() != null
                && actual.transferAmountCent() != expectedAmountCent) {
            violations.add("AMOUNT_MISMATCH");
        }
        compareWhenPresent(actual.openid(), expectedOpenid,
                "OPENID", violations);
        return new Validation(violations);
    }

    private static void requireEqual(
            String actual,
            String expected,
            String field,
            List<String> violations) {
        if (actual == null || actual.isBlank()) {
            violations.add(field + "_MISSING");
        } else if (!actual.equals(expected)) {
            violations.add(field + "_MISMATCH");
        }
    }

    private static void compareWhenPresent(
            String actual,
            String expected,
            String field,
            List<String> violations) {
        if (actual != null && !actual.equals(expected)) {
            violations.add(field + "_MISMATCH");
        }
    }

    public record Validation(List<String> violations) {

        public Validation {
            violations = List.copyOf(violations);
        }

        public boolean trusted() {
            return violations.isEmpty();
        }

        public String safeSummary() {
            return trusted() ? "MATCHED" : String.join(",", violations);
        }
    }
}
