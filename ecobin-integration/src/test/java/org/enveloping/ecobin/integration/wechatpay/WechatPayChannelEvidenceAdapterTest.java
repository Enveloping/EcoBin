package org.enveloping.ecobin.integration.wechatpay;

import org.enveloping.ecobin.funds.api.port.MerchantTransferChannelPort;
import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class WechatPayChannelEvidenceAdapterTest {

    private final ObjectMapper mapper = new ObjectMapper();
    private final WechatPayApiV3Client client = mock(WechatPayApiV3Client.class);

    @Test
    void nativeQueryKeepsWechatOrderIdentityAndAmount() throws Exception {
        when(client.get("/v3/pay/transactions/out-trade-no/NP123?mchid=190001"))
                .thenReturn(mapper.readTree("""
                        {
                          "appid":"wx-app-1",
                          "mchid":"190001",
                          "out_trade_no":"NP123",
                          "transaction_id":"WX420001",
                          "trade_state":"SUCCESS",
                          "trade_state_desc":"success",
                          "success_time":"2026-08-03T10:00:00+08:00",
                          "amount":{"total":1234,"currency":"CNY"}
                        }
                        """));

        var result = new WechatPayNativePaymentAdapter(client, mapper).query(
                new NativePaymentChannelPort.NativePaymentQuery(
                        "190001", "NP123"));

        assertEquals("190001", result.mchid());
        assertEquals("wx-app-1", result.appid());
        assertEquals("NP123", result.outTradeNo());
        assertEquals(1234L, result.totalAmountCent());
        assertEquals("CNY", result.currency());
    }

    @Test
    void nativeRefundIsAnExplicitTerminalReconciliationOutcome()
            throws Exception {
        when(client.get("/v3/pay/transactions/out-trade-no/NPREFUND?mchid=190001"))
                .thenReturn(mapper.readTree("""
                        {
                          "appid":"wx-app-1",
                          "mchid":"190001",
                          "out_trade_no":"NPREFUND",
                          "transaction_id":"WXREFUND1",
                          "trade_state":"REFUND",
                          "amount":{"total":1234,"currency":"CNY"}
                        }
                        """));

        var result = new WechatPayNativePaymentAdapter(client, mapper).query(
                new NativePaymentChannelPort.NativePaymentQuery(
                        "190001", "NPREFUND"));

        assertEquals(
                NativePaymentChannelPort.NativePaymentResult.Outcome.REFUNDED,
                result.outcome());
        assertEquals(1234L, result.totalAmountCent());
    }

    @Test
    void reusedNativeTradeNumberRequiresAuthoritativeQuery() {
        when(client.post(
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.any()))
                .thenThrow(new WechatPayApiException(
                        400, "OUT_TRADE_NO_USED", "order already exists"));

        var result = new WechatPayNativePaymentAdapter(client, mapper)
                .create(new NativePaymentChannelPort.NativePaymentRequest(
                        "190001", "wx-app-1", "NPREUSED", 1234,
                        "test", Instant.parse("2026-08-03T12:00:00Z"),
                        "https://example.com/wechat/native-notify"));

        assertEquals(
                NativePaymentChannelPort.NativePaymentResult.Outcome
                        .ORDER_ALREADY_EXISTS,
                result.outcome());
    }

    @Test
    void transferQueryKeepsWechatBillIdentityRecipientAndAmount()
            throws Exception {
        when(client.get("/v3/fund-app/mch-transfer/transfer-bills/"
                + "out-bill-no/MT123"))
                .thenReturn(mapper.readTree("""
                        {
                          "mch_id":"190001",
                          "out_bill_no":"MT123",
                          "transfer_bill_no":"WXTR420001",
                          "appid":"wx-app-1",
                          "state":"SUCCESS",
                          "transfer_amount":888,
                          "transfer_remark":"test",
                          "openid":"openid-1",
                          "create_time":"2026-08-03T10:00:00+08:00",
                          "update_time":"2026-08-03T10:01:00+08:00"
                        }
                        """));

        var result = new WechatPayMerchantTransferAdapter(client, mapper).query(
                new MerchantTransferChannelPort.MerchantTransferQuery(
                        "190001", "MT123"));

        assertEquals("190001", result.mchid());
        assertEquals("MT123", result.outBillNo());
        assertEquals("wx-app-1", result.appid());
        assertEquals(888L, result.transferAmountCent());
        assertEquals("openid-1", result.openid());
    }

    @Test
    void transferQueryDistinguishesAConfirmedMissingOriginalBill() {
        when(client.get("/v3/fund-app/mch-transfer/transfer-bills/"
                + "out-bill-no/MT404"))
                .thenThrow(new WechatPayApiException(
                        404, "NOT_FOUND", "record missing"));

        var result = new WechatPayMerchantTransferAdapter(client, mapper).query(
                new MerchantTransferChannelPort.MerchantTransferQuery(
                        "190001", "MT404"));

        assertEquals(
                MerchantTransferChannelPort.MerchantTransferResult.Outcome.NOT_FOUND,
                result.outcome());
        assertNull(result.channelState(),
                "HTTP 404 is an API result, not a WeChat bill state");
        assertEquals("NOT_FOUND", result.errorCode());
    }

    @Test
    void uncertainSubmitErrorsRequireQueryBeforeAnyResubmission() {
        when(client.post(
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.any()))
                .thenThrow(new WechatPayApiException(
                        400, "ALREADY_EXISTS", "bill may already exist"));

        var result = new WechatPayMerchantTransferAdapter(client, mapper)
                .submit(transferRequest("MT-UNCERTAIN"));

        assertEquals(
                MerchantTransferChannelPort.MerchantTransferResult.Outcome
                        .RETRYABLE_FAILURE,
                result.outcome());
        assertNull(result.channelState(),
                "API errors must not masquerade as WeChat bill states");
        assertEquals("ALREADY_EXISTS", result.errorCode());
    }

    @Test
    void invalidOriginalRequestIsARecoverableOperationalBlock() {
        when(client.post(
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.any()))
                .thenThrow(new WechatPayApiException(
                        400, "PARAM_ERROR", "invalid parameter"));

        var result = new WechatPayMerchantTransferAdapter(client, mapper)
                .submit(transferRequest("MT-PERMANENT"));

        assertEquals(
                MerchantTransferChannelPort.MerchantTransferResult.Outcome
                        .PERMANENT_FAILURE,
                result.outcome());
        assertNull(result.channelState(),
                "request rejection is not a WeChat bill state");
        assertEquals("PARAM_ERROR", result.errorCode());
    }

    @Test
    void notEnoughIsKeptAsAnApiErrorWithoutInventingABillState() {
        when(client.post(
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.any()))
                .thenThrow(new WechatPayApiException(
                        403, "NOT_ENOUGH", "merchant balance insufficient"));

        var result = new WechatPayMerchantTransferAdapter(client, mapper)
                .submit(transferRequest("MT-NOT-ENOUGH"));

        assertEquals(
                MerchantTransferChannelPort.MerchantTransferResult.Outcome
                        .NOT_ENOUGH,
                result.outcome());
        assertNull(result.channelState());
        assertEquals("NOT_ENOUGH", result.errorCode());
    }

    @Test
    void responseSignatureFailureBlocksAutomaticFundsProcessing() {
        when(client.post(
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.any()))
                .thenThrow(new WechatPayApiException(
                        502, "SIGNATURE_ERROR",
                        "response signature verification failed"));

        var result = new WechatPayMerchantTransferAdapter(client, mapper)
                .submit(transferRequest("MT-SIGNATURE-ERROR"));

        assertEquals(
                MerchantTransferChannelPort.MerchantTransferResult.Outcome
                        .PERMANENT_FAILURE,
                result.outcome());
        assertNull(result.channelState());
        assertEquals("SIGNATURE_ERROR", result.errorCode());
    }

    private MerchantTransferChannelPort.MerchantTransferRequest transferRequest(
            String outBillNo) {
        return new MerchantTransferChannelPort.MerchantTransferRequest(
                "190001", "wx-app-1", outBillNo, "openid-1", 100L,
                "1001", "RECYCLED_GOODS_NAME", "MIXED_RECYCLABLES",
                "test transfer", "STANDARD",
                "https://example.com/wechat/transfer-notify");
    }
}
