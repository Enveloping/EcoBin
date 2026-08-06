package org.enveloping.ecobin.funds.application.withdrawal;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class MerchantTransferStatusPresentationTest {

    @Test
    void notEnoughExplainsThePauseAndPreservedFrozenFunds() {
        MerchantTransferStatusPresentation presentation =
                MerchantTransferStatusPresentation.from(
                        "NOT_ENOUGH");

        assertEquals("NOT_ENOUGH", presentation.errorCode());
        assertEquals(
                "微信返回出资商户余额不足；提现资金仍保持冻结，系统会按出款闸门和原商户单号继续处理",
                presentation.message());
    }

    @Test
    void permanentConfigurationFailureGivesAnOperatorAction() {
        MerchantTransferStatusPresentation presentation =
                MerchantTransferStatusPresentation.from(
                        "NO_AUTH");

        assertEquals("NO_AUTH", presentation.errorCode());
        assertEquals(
                "微信商家转账权限不可用，系统已停止自动提交，请平台管理员检查商户产品权限",
                presentation.message());
    }

    @Test
    void ordinaryChannelStateHasNoSyntheticFailure() {
        MerchantTransferStatusPresentation presentation =
                MerchantTransferStatusPresentation.from(null);

        assertNull(presentation.errorCode());
        assertNull(presentation.message());
    }

    @Test
    void officialRateLimitCodeExplainsUncertainResultAndBackoff() {
        MerchantTransferStatusPresentation presentation =
                MerchantTransferStatusPresentation.from(
                        "RATELIMIT_EXCEEDED");

        assertEquals("RATELIMIT_EXCEEDED", presentation.errorCode());
        assertEquals(
                "微信接口暂时不可用或结果不确定，系统会使用原商户单号查单并按退避策略重试",
                presentation.message());
    }

    @Test
    void unknownCodeDoesNotClaimThatRecoveryHasStopped() {
        MerchantTransferStatusPresentation presentation =
                MerchantTransferStatusPresentation.from(
                        "FUTURE_WECHAT_ERROR");

        assertEquals("FUTURE_WECHAT_ERROR", presentation.errorCode());
        assertEquals(
                "微信渠道返回未识别错误 FUTURE_WECHAT_ERROR，结果尚不明确；系统会使用原商户单号查单并按退避策略处理，请平台管理员持续关注",
                presentation.message());
    }
}
