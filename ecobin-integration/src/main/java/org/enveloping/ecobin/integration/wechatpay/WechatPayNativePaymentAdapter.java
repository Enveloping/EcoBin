package org.enveloping.ecobin.integration.wechatpay;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;
import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.format.DateTimeFormatter;

@Component
@ConditionalOnProperty(
        prefix = "ecobin.external", name = "mode", havingValue = "real")
public class WechatPayNativePaymentAdapter
        implements NativePaymentChannelPort {

    private final WechatPayApiV3Client client;
    private final ObjectMapper mapper;

    public WechatPayNativePaymentAdapter(
            WechatPayApiV3Client client, ObjectMapper mapper) {
        this.client = client;
        this.mapper = mapper;
    }

    @Override
    public NativePaymentResult create(NativePaymentRequest request) {
        ObjectNode body = mapper.createObjectNode();
        body.put("appid", request.appid());
        body.put("mchid", request.mchid());
        body.put("description", request.description());
        body.put("out_trade_no", request.outTradeNo());
        body.put("time_expire", DateTimeFormatter.ISO_INSTANT
                .format(request.expiresAt()));
        body.put("notify_url", request.notifyUrl());
        ObjectNode amount = body.putObject("amount");
        amount.put("total", request.amountCent());
        amount.put("currency", "CNY");
        try {
            JsonNode response = client.post(
                    "/v3/pay/transactions/native", body);
            return new NativePaymentResult(
                    NativePaymentResult.Outcome.ACCEPTED,
                    "NOTPAY", WechatPayApiV3Client.text(response, "code_url"),
                    null, null, null, Instant.now());
        } catch (WechatPayApiException failure) {
            return error(failure);
        }
    }

    @Override
    public NativePaymentResult query(NativePaymentQuery query) {
        String path = "/v3/pay/transactions/out-trade-no/"
                + encode(query.outTradeNo()) + "?mchid=" + encode(query.mchid());
        try {
            return map(client.get(path));
        } catch (WechatPayApiException failure) {
            return error(failure);
        }
    }

    @Override
    public NativePaymentResult close(NativePaymentQuery query) {
        ObjectNode body = mapper.createObjectNode();
        body.put("mchid", query.mchid());
        try {
            client.post("/v3/pay/transactions/out-trade-no/"
                    + encode(query.outTradeNo()) + "/close", body);
            return new NativePaymentResult(
                    NativePaymentResult.Outcome.CLOSED,
                    "CLOSED", null, null, null, null, Instant.now());
        } catch (WechatPayApiException failure) {
            return error(failure);
        }
    }

    private NativePaymentResult map(JsonNode response) {
        String state = WechatPayApiV3Client.text(response, "trade_state");
        NativePaymentResult.Outcome outcome = switch (
                state == null ? "" : state) {
            case "SUCCESS" -> NativePaymentResult.Outcome.SUCCEEDED;
            case "CLOSED", "REVOKED", "PAYERROR" ->
                    NativePaymentResult.Outcome.CLOSED;
            case "NOTPAY", "USERPAYING", "REFUND" ->
                    NativePaymentResult.Outcome.ACCEPTED;
            default -> NativePaymentResult.Outcome.UNKNOWN_STATE;
        };
        JsonNode amount = response == null ? null : response.get("amount");
        return new NativePaymentResult(
                outcome, state, null,
                WechatPayApiV3Client.text(response, "transaction_id"),
                null, WechatPayApiV3Client.text(response, "trade_state_desc"),
                parseInstant(WechatPayApiV3Client.text(response, "success_time")),
                WechatPayApiV3Client.text(response, "mchid"),
                WechatPayApiV3Client.text(response, "appid"),
                WechatPayApiV3Client.text(response, "out_trade_no"),
                longValue(amount, "total"),
                WechatPayApiV3Client.text(amount, "currency"));
    }

    private static NativePaymentResult error(WechatPayApiException failure) {
        boolean terminal = !failure.retryable()
                && ("ORDER_CLOSED".equals(failure.code())
                || "ORDER_REVERSED".equals(failure.code()));
        return new NativePaymentResult(
                terminal ? NativePaymentResult.Outcome.CLOSED
                        : failure.retryable()
                        ? NativePaymentResult.Outcome.RETRYABLE_FAILURE
                        : "ORDER_NOT_EXIST".equals(failure.code())
                        ? NativePaymentResult.Outcome.NOT_FOUND
                        : NativePaymentResult.Outcome.PERMANENT_FAILURE,
                "API_ERROR", null, null, failure.code(),
                failure.getMessage(), Instant.now());
    }

    private static Long longValue(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        return value == null || !value.canConvertToLong()
                ? null : value.asLong();
    }

    private static Instant parseInstant(String value) {
        if (value == null || value.isBlank()) return Instant.now();
        try {
            return Instant.parse(value);
        } catch (RuntimeException ignored) {
            return java.time.OffsetDateTime.parse(value).toInstant();
        }
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8)
                .replace("+", "%20");
    }
}
