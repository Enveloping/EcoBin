package org.enveloping.ecobin.integration.wechatpay;

import org.enveloping.ecobin.funds.api.port
        .MerchantTransferAuthorizationChannelPort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class WechatPayMerchantTransferAuthorizationAdapterTest {

    private final ObjectMapper mapper = new ObjectMapper();
    private final WechatPayApiV3Client client =
            mock(WechatPayApiV3Client.class);

    @BeforeEach
    void configureMerchant() {
        when(client.usesMerchant("190001")).thenReturn(true);
    }

    @Test
    void createFreezesOfficialRequestFieldsAndKeepsLaunchPackage()
            throws Exception {
        ArgumentCaptor<JsonNode> body = ArgumentCaptor.forClass(JsonNode.class);
        when(client.post(eq(
                "/v3/fund-app/mch-transfer/user-confirm-authorization"),
                body.capture())).thenReturn(mapper.readTree("""
                        {
                          "out_authorization_no":"AU12345678",
                          "state":"WAIT_USER_CONFIRM",
                          "create_time":"2026-08-06T10:00:00+08:00",
                          "package_info":"authorization-package"
                        }
                        """));
        var request = new MerchantTransferAuthorizationChannelPort
                .AuthorizationRequest(
                "190001", "AU12345678", "wx-app-1", "openid-1",
                "1001", "金收宝用户-12345678", null,
                "https://example.com/api/v1/wechat-pay/notifications/"
                        + "merchant-transfer-authorizations");

        var result = new WechatPayMerchantTransferAuthorizationAdapter(
                client, mapper).create(request);

        assertEquals(
                MerchantTransferAuthorizationChannelPort.AuthorizationResult
                        .Outcome.WAIT_USER_CONFIRM,
                result.outcome());
        assertEquals("authorization-package", result.packageInfo());
        assertEquals(request.notifyUrl(), body.getValue()
                .path("authorization_notify_url").asText());
        assertEquals(request.userDisplayName(), body.getValue()
                .path("user_display_name").asText());
    }

    @Test
    void queryKeepsCompleteActiveAuthorizationIdentity() throws Exception {
        when(client.get(
                "/v3/fund-app/mch-transfer/user-confirm-authorization/"
                        + "out-authorization-no/AU12345678"))
                .thenReturn(mapper.readTree("""
                        {
                          "out_authorization_no":"AU12345678",
                          "authorization_id":"WXAUTH123",
                          "appid":"wx-app-1",
                          "openid":"openid-1",
                          "transfer_scene_id":"1001",
                          "user_display_name":"金收宝用户-12345678",
                          "state":"TAKING_EFFECT",
                          "create_time":"2026-08-06T10:00:00+08:00",
                          "authorize_time":"2026-08-06T10:01:00+08:00"
                        }
                        """));

        var result = new WechatPayMerchantTransferAuthorizationAdapter(
                client, mapper).query(
                new MerchantTransferAuthorizationChannelPort
                        .AuthorizationQuery(
                        "190001", "AU12345678", "wx-app-1", "openid-1",
                        "1001", "金收宝用户-12345678", null, null));

        assertEquals(
                MerchantTransferAuthorizationChannelPort.AuthorizationResult
                        .Outcome.ACTIVE,
                result.outcome());
        assertEquals("WXAUTH123", result.authorizationId());
        assertEquals("wx-app-1", result.appid());
        assertEquals("openid-1", result.openid());
        assertEquals("1001", result.sceneId());
        assertEquals(Instant.parse("2026-08-06T02:00:00Z"),
                result.channelCreatedAt());
        assertEquals(Instant.parse("2026-08-06T02:01:00Z"),
                result.authorizedAt());
    }

    @Test
    void queryKeepsWaitingStateWithoutRequestingAReplacementLaunchPackage()
            throws Exception {
        when(client.get(
                "/v3/fund-app/mch-transfer/user-confirm-authorization/"
                        + "out-authorization-no/AU12345678"))
                .thenReturn(mapper.readTree("""
                        {
                          "out_authorization_no":"AU12345678",
                          "appid":"wx-app-1",
                          "openid":"openid-1",
                          "user_display_name":"金收宝用户-12345678",
                          "state":"WAIT_USER_CONFIRM"
                        }
                        """));

        var result = new WechatPayMerchantTransferAuthorizationAdapter(
                client, mapper).query(
                new MerchantTransferAuthorizationChannelPort
                        .AuthorizationQuery(
                        "190001", "AU12345678", "wx-app-1", "openid-1",
                        "1001", "金收宝用户-12345678", null, null));

        assertEquals(
                MerchantTransferAuthorizationChannelPort.AuthorizationResult
                        .Outcome.WAIT_USER_CONFIRM,
                result.outcome());
        assertNull(result.packageInfo());
        assertNull(result.sceneId());
        assertNull(result.channelCreatedAt());
    }

    @Test
    void queryTreatsInvalidRequestAsPermanentInsteadOfRetrying() {
        when(client.get(
                "/v3/fund-app/mch-transfer/user-confirm-authorization/"
                        + "out-authorization-no/AU12345678"))
                .thenThrow(new WechatPayApiException(
                        400, "INVALID_REQUEST",
                        "用户未确认授权或已取消授权，不支持当前操作"));

        var result = new WechatPayMerchantTransferAuthorizationAdapter(
                client, mapper).query(
                new MerchantTransferAuthorizationChannelPort
                        .AuthorizationQuery(
                        "190001", "AU12345678", "wx-app-1", "openid-1",
                        "1001", "金收宝用户-12345678", null, null));

        assertEquals(
                MerchantTransferAuthorizationChannelPort.AuthorizationResult
                        .Outcome.PERMANENT_FAILURE,
                result.outcome());
        assertEquals(400, result.httpStatus());
        assertEquals("INVALID_REQUEST", result.errorCode());
    }

    @Test
    void createKeepsWechatStatusCodeAndDiagnosticForOperations() {
        when(client.post(eq(
                "/v3/fund-app/mch-transfer/user-confirm-authorization"),
                org.mockito.ArgumentMatchers.any(JsonNode.class)))
                .thenThrow(new WechatPayApiException(
                        400, "PARAM_ERROR", "transfer_scene_id 参数错误"));
        var request = new MerchantTransferAuthorizationChannelPort
                .AuthorizationRequest(
                "190001", "AU12345678", "wx-app-1", "openid-1",
                "1001", "金收宝用户-12345678", null,
                "https://example.com/api/v1/wechat-pay/notifications/"
                        + "merchant-transfer-authorizations");

        var result = new WechatPayMerchantTransferAuthorizationAdapter(
                client, mapper).create(request);

        assertEquals(
                MerchantTransferAuthorizationChannelPort.AuthorizationResult
                        .Outcome.PERMANENT_FAILURE,
                result.outcome());
        assertEquals(400, result.httpStatus());
        assertEquals("PARAM_ERROR", result.errorCode());
        assertEquals("transfer_scene_id 参数错误", result.diagnostic());
    }

    @Test
    void untrustedCreateResponseRemainsUnknownInsteadOfReleasingTheOrder() {
        when(client.post(eq(
                "/v3/fund-app/mch-transfer/user-confirm-authorization"),
                org.mockito.ArgumentMatchers.any(JsonNode.class)))
                .thenThrow(new WechatPayApiException(
                        502, "SIGNATURE_ERROR", "微信支付响应验签失败"));
        var request = new MerchantTransferAuthorizationChannelPort
                .AuthorizationRequest(
                "190001", "AU12345678", "wx-app-1", "openid-1",
                "1001", "JSBUser1234567890abcdef", null,
                "https://example.com/api/v1/wechat-pay/notifications/"
                        + "merchant-transfer-authorizations");

        var result = new WechatPayMerchantTransferAuthorizationAdapter(
                client, mapper).create(request);

        assertEquals(
                MerchantTransferAuthorizationChannelPort.AuthorizationResult
                        .Outcome.UNKNOWN_STATE,
                result.outcome());
        assertEquals("SIGNATURE_ERROR", result.errorCode());
    }
}
