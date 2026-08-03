package org.enveloping.ecobin.integration.wechatpay;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;
import org.enveloping.ecobin.funds.api.port.MerchantTransferChannelPort;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.OffsetDateTime;

@Component
@ConditionalOnProperty(
        prefix = "ecobin.external", name = "mode", havingValue = "real")
public class WechatPayMerchantTransferAdapter
        implements MerchantTransferChannelPort {

    private final WechatPayApiV3Client client;
    private final ObjectMapper mapper;

    public WechatPayMerchantTransferAdapter(
            WechatPayApiV3Client client, ObjectMapper mapper) {
        this.client = client;
        this.mapper = mapper;
    }

    @Override
    public MerchantTransferResult submit(MerchantTransferRequest request) {
        ObjectNode body = mapper.createObjectNode();
        body.put("appid", request.appid());
        body.put("out_bill_no", request.outBillNo());
        body.put("transfer_scene_id", request.sceneId());
        body.put("openid", request.openid());
        body.put("transfer_amount", request.amountCent());
        body.put("transfer_remark", request.remark());
        body.put("notify_url", request.notifyUrl());
        ObjectNode report = body.putArray("transfer_scene_report_infos")
                .addObject();
        report.put("info_type", reportType(request.reportType()));
        report.put("info_content", reportContent(request.reportContent()));
        ObjectNode style = body.putObject("user_recv_style");
        style.put("type", styleType(request.transferPageStyle()));
        try {
            return map(client.post(
                    "/v3/fund-app/mch-transfer/transfer-bills", body));
        } catch (WechatPayApiException failure) {
            return error(failure);
        }
    }

    @Override
    public MerchantTransferResult query(MerchantTransferQuery query) {
        try {
            return map(client.get(base(query.outBillNo())));
        } catch (WechatPayApiException failure) {
            return queryError(failure);
        }
    }

    @Override
    public MerchantTransferResult cancel(MerchantTransferQuery query) {
        try {
            return map(client.post(base(query.outBillNo()) + "/cancel",
                    mapper.createObjectNode()));
        } catch (WechatPayApiException failure) {
            return error(failure);
        }
    }

    private MerchantTransferResult map(JsonNode response) {
        String state = WechatPayApiV3Client.text(response, "state");
        MerchantTransferResult.Outcome outcome = switch (
                state == null ? "" : state) {
            case "WAIT_USER_CONFIRM" ->
                    MerchantTransferResult.Outcome.WAIT_USER_CONFIRM;
            case "SUCCESS" -> MerchantTransferResult.Outcome.SUCCESS;
            case "FAIL" -> MerchantTransferResult.Outcome.FAIL;
            case "CANCELLED" -> MerchantTransferResult.Outcome.CANCELLED;
            case "ACCEPTED", "PROCESSING", "TRANSFERING", "CANCELING" ->
                    MerchantTransferResult.Outcome.PROCESSING;
            default -> MerchantTransferResult.Outcome.UNKNOWN_STATE;
        };
        return new MerchantTransferResult(
                outcome, state,
                WechatPayApiV3Client.text(response, "transfer_bill_no"),
                WechatPayApiV3Client.text(response, "package_info"),
                null, WechatPayApiV3Client.text(response, "fail_reason"),
                null, parseInstant(response),
                WechatPayApiV3Client.text(response, "mch_id"),
                WechatPayApiV3Client.text(response, "out_bill_no"),
                WechatPayApiV3Client.text(response, "appid"),
                longValue(response, "transfer_amount"),
                WechatPayApiV3Client.text(response, "openid"));
    }

    private static MerchantTransferResult error(
            WechatPayApiException failure) {
        MerchantTransferResult.Outcome outcome;
        if ("NOT_ENOUGH".equals(failure.code())) {
            outcome = MerchantTransferResult.Outcome.NOT_ENOUGH;
        } else if (isPermanentRequestError(failure.code())) {
            outcome = MerchantTransferResult.Outcome.PERMANENT_FAILURE;
        } else {
            // SYSTEM_ERROR、限频、ALREADY_EXISTS 及新增错误码的结果
            // 都不能被当成明确失败；业务层会先用原商户单号查单。
            outcome = MerchantTransferResult.Outcome.RETRYABLE_FAILURE;
        }
        return new MerchantTransferResult(
                outcome, "API_ERROR", null, null, failure.code(),
                null, failure.getMessage(), Instant.now());
    }

    private static MerchantTransferResult queryError(
            WechatPayApiException failure) {
        if (failure.status() == 404 && "NOT_FOUND".equals(failure.code())) {
            return new MerchantTransferResult(
                    MerchantTransferResult.Outcome.NOT_FOUND,
                    "NOT_FOUND", null, null, failure.code(), null,
                    failure.getMessage(), Instant.now());
        }
        return error(failure);
    }

    private static boolean isPermanentRequestError(String code) {
        return java.util.Set.of(
                "PARAM_ERROR", "INVALID_REQUEST", "NO_AUTH", "SIGN_ERROR")
                .contains(code);
    }

    private static Long longValue(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        return value == null || !value.canConvertToLong()
                ? null : value.asLong();
    }

    private static Instant parseInstant(JsonNode response) {
        String value = WechatPayApiV3Client.text(response, "update_time");
        if (value == null) {
            value = WechatPayApiV3Client.text(response, "create_time");
        }
        if (value == null || value.isBlank()) return Instant.now();
        return OffsetDateTime.parse(value).toInstant();
    }

    private static String reportType(String internal) {
        return "RECYCLED_GOODS_NAME".equals(internal)
                ? "回收商品名称" : internal;
    }

    private static String reportContent(String internal) {
        return "MIXED_RECYCLABLES".equals(internal)
                ? "混合可回收物" : internal;
    }

    private static String styleType(String internal) {
        return "STANDARD".equals(internal) ? "CONFIRM_PAGE" : internal;
    }

    private static String base(String outBillNo) {
        return "/v3/fund-app/mch-transfer/transfer-bills/out-bill-no/"
                + URLEncoder.encode(outBillNo, StandardCharsets.UTF_8)
                .replace("+", "%20");
    }
}
