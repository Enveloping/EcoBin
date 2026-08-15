package org.enveloping.ecobin.funds.application.channel;

import org.enveloping.ecobin.funds.api.port.MerchantTransferChannelPort.MerchantTransferResult;
import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort.NativePaymentResult;
import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class WechatChannelEvidencePolicyTest {

    @Test
    void nativeSuccessRejectsWechatAmountOrIdentityMismatch() {
        NativePaymentResult result = new NativePaymentResult(
                NativePaymentResult.Outcome.SUCCEEDED,
                "SUCCESS", null, "WX420001", null, null,
                Instant.parse("2026-08-03T02:00:00Z"),
                "wrong-mchid", "wx-app-1", "NP123", 999L, "CNY");

        var validation = WechatChannelEvidencePolicy.validateNativeQuery(
                "190001", "wx-app-1", "NP123", 1000L, result);

        assertFalse(validation.trusted());
        assertTrue(validation.violations().contains("MCHID_MISMATCH"));
        assertTrue(validation.violations().contains("AMOUNT_MISMATCH"));
    }

    @Test
    void transferSuccessRejectsWechatRecipientOrAmountMismatch() {
        MerchantTransferResult result = new MerchantTransferResult(
                MerchantTransferResult.Outcome.SUCCESS,
                "SUCCESS", "WXTR420001", null, null, null, null,
                Instant.parse("2026-08-03T02:00:00Z"),
                "190001", "MT123", "wx-app-1", 1001L, "wrong-openid");

        var validation = WechatChannelEvidencePolicy.validateTransferQuery(
                "190001", "wx-app-1", "MT123", "WXTR420001",
                1000L, "openid-1", result);

        assertFalse(validation.trusted());
        assertTrue(validation.violations().contains("AMOUNT_MISMATCH"));
        assertTrue(validation.violations().contains("OPENID_MISMATCH"));
    }

    @Test
    void exactTransferQueryEvidenceIsTrusted() {
        MerchantTransferResult result = new MerchantTransferResult(
                MerchantTransferResult.Outcome.SUCCESS,
                "SUCCESS", "WXTR420001", null, null, null, null,
                Instant.parse("2026-08-03T02:00:00Z"),
                "190001", "MT123", "wx-app-1", 1000L, "openid-1");

        assertTrue(WechatChannelEvidencePolicy.validateTransferQuery(
                "190001", "wx-app-1", "MT123", "WXTR420001",
                1000L, "openid-1", result).trusted());
    }

    @Test
    void transferQueryWithoutOptionalWechatRecipientIsTrusted() {
        MerchantTransferResult result = new MerchantTransferResult(
                MerchantTransferResult.Outcome.SUCCESS,
                "SUCCESS", "WXTR420001", null, null, null, null,
                Instant.parse("2026-08-03T02:00:00Z"),
                "190001", "MT123", "wx-app-1", 1000L, null);

        var validation = WechatChannelEvidencePolicy.validateTransferQuery(
                "190001", "wx-app-1", "MT123", "WXTR420001",
                1000L, "openid-1", result);

        assertTrue(validation.trusted());
        assertFalse(validation.violations().contains("OPENID_MISSING"));
    }

    @Test
    void transferQueryWithoutRequiredAmountIsNotEvidence() {
        MerchantTransferResult result = new MerchantTransferResult(
                MerchantTransferResult.Outcome.SUCCESS,
                "SUCCESS", "WXTR420001", null, null, null, null,
                Instant.parse("2026-08-03T02:00:00Z"),
                "190001", "MT123", "wx-app-1", null, null);

        var validation = WechatChannelEvidencePolicy.validateTransferQuery(
                "190001", "wx-app-1", "MT123", "WXTR420001",
                1000L, "openid-1", result);

        assertFalse(validation.trusted());
        assertTrue(validation.violations().contains("AMOUNT_MISSING"));
    }

    @Test
    void authorizedTransferRejectsUserConfirmationState() {
        assertFalse(WechatChannelEvidencePolicy
                .isTransferStateCompatibleWithCollectionMode(
                        "AUTHORIZED",
                        MerchantTransferResult.Outcome.WAIT_USER_CONFIRM));
        assertTrue(WechatChannelEvidencePolicy
                .isTransferStateCompatibleWithCollectionMode(
                        "AUTHORIZED",
                        MerchantTransferResult.Outcome.PROCESSING));
        assertTrue(WechatChannelEvidencePolicy
                .isTransferStateCompatibleWithCollectionMode(
                        "USER_CONFIRM",
                        MerchantTransferResult.Outcome.WAIT_USER_CONFIRM));
    }
}
