package org.enveloping.ecobin.integration.wechatpay;

import org.enveloping.ecobin.funds.api.port
        .MerchantTransferAuthorizationChannelPort;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.OffsetDateTime;

@Component
@ConditionalOnProperty(
        prefix = "ecobin.external", name = "mode", havingValue = "real")
public class WechatPayMerchantTransferAuthorizationAdapter
        implements MerchantTransferAuthorizationChannelPort {

    private static final String BASE =
            "/v3/fund-app/mch-transfer/user-confirm-authorization";

    private final WechatPayApiV3Client client;
    private final ObjectMapper mapper;

    public WechatPayMerchantTransferAuthorizationAdapter(
            WechatPayApiV3Client client, ObjectMapper mapper) {
        this.client = client;
        this.mapper = mapper;
    }

    @Override
    public AuthorizationResult create(AuthorizationRequest request) {
        if (!client.usesMerchant(request.mchid())) {
            return merchantMismatch();
        }
        ObjectNode body = mapper.createObjectNode();
        body.put("out_authorization_no", request.outAuthorizationNo());
        body.put("appid", request.appid());
        body.put("openid", request.openid());
        body.put("transfer_scene_id", request.sceneId());
        body.put("user_display_name", request.userDisplayName());
        if (request.userRecvPerception() != null) {
            body.put("user_recv_perception", request.userRecvPerception());
        }
        body.put("authorization_notify_url", request.notifyUrl());
        try {
            return map(client.post(BASE, body));
        } catch (WechatPayApiException failure) {
            return createError(failure);
        }
    }

    @Override
    public AuthorizationResult query(AuthorizationQuery query) {
        if (!client.usesMerchant(query.mchid())) {
            return merchantMismatch();
        }
        try {
            return map(client.get(BASE + "/out-authorization-no/"
                    + encode(query.outAuthorizationNo())
                    + "?is_display_authorization=true"));
        } catch (WechatPayApiException failure) {
            if (failure.status() == 404
                    && "NOT_FOUND".equals(failure.code())) {
                return error(
                        AuthorizationResult.Outcome.NOT_FOUND, failure);
            }
            return permanentOrRetryable(failure);
        }
    }

    private static AuthorizationResult map(JsonNode response) {
        String state = WechatPayApiV3Client.text(response, "state");
        AuthorizationResult.Outcome outcome = switch (
                state == null ? "" : state) {
            case "WAIT_USER_CONFIRM" ->
                    AuthorizationResult.Outcome.WAIT_USER_CONFIRM;
            case "TAKING_EFFECT" -> AuthorizationResult.Outcome.ACTIVE;
            case "CLOSED" -> AuthorizationResult.Outcome.CLOSED;
            default -> AuthorizationResult.Outcome.UNKNOWN_STATE;
        };
        JsonNode closeInfo = response == null
                ? null : response.get("close_info");
        Instant observedAt = Instant.now();
        return new AuthorizationResult(
                outcome, state,
                WechatPayApiV3Client.text(
                        response, "out_authorization_no"),
                WechatPayApiV3Client.text(response, "authorization_id"),
                WechatPayApiV3Client.text(response, "appid"),
                WechatPayApiV3Client.text(response, "openid"),
                WechatPayApiV3Client.text(response, "transfer_scene_id"),
                WechatPayApiV3Client.text(response, "user_display_name"),
                WechatPayApiV3Client.text(
                        response, "user_recv_perception"),
                WechatPayApiV3Client.text(response, "package_info"),
                WechatPayApiV3Client.text(closeInfo, "close_reason"),
                instant(response, "create_time"),
                instant(response, "authorize_time"),
                instant(closeInfo, "close_time"),
                null, null, observedAt);
    }

    private static AuthorizationResult createError(
            WechatPayApiException failure) {
        if ("INVALID_REQUEST".equals(failure.code())) {
            return error(
                    AuthorizationResult.Outcome.RETRYABLE_FAILURE, failure);
        }
        return permanentOrRetryable(failure);
    }

    private static AuthorizationResult permanentOrRetryable(
            WechatPayApiException failure) {
        boolean permanent = java.util.Set.of(
                "PARAM_ERROR", "NO_AUTH", "SIGN_ERROR",
                "SIGNATURE_ERROR", "RESPONSE_SIGNATURE_INVALID")
                .contains(failure.code());
        return error(permanent
                ? AuthorizationResult.Outcome.PERMANENT_FAILURE
                : AuthorizationResult.Outcome.RETRYABLE_FAILURE, failure);
    }

    private static AuthorizationResult merchantMismatch() {
        return new AuthorizationResult(
                AuthorizationResult.Outcome.PERMANENT_FAILURE,
                null, null, null, null, null, null, null, null,
                null, null, null, null, null, "MCHID_MISMATCH",
                "configured merchant does not match request merchant",
                Instant.now());
    }

    private static AuthorizationResult error(
            AuthorizationResult.Outcome outcome,
            WechatPayApiException failure) {
        return new AuthorizationResult(
                outcome, null, null, null, null, null, null, null, null,
                null, null, null, null, null, failure.code(),
                failure.status(), failure.getMessage(), Instant.now());
    }

    private static Instant instant(JsonNode node, String field) {
        String value = WechatPayApiV3Client.text(node, field);
        return value == null || value.isBlank()
                ? null : OffsetDateTime.parse(value).toInstant();
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8)
                .replace("+", "%20");
    }
}
