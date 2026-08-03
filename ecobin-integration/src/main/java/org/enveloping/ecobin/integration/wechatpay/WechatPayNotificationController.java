package org.enveloping.ecobin.integration.wechatpay;

import org.enveloping.ecobin.funds.api.port.WechatPayNotificationPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxExecutionLane;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.UUID;

@RestController
@ConditionalOnProperty(
        prefix = "ecobin.external", name = "mode", havingValue = "real")
public class WechatPayNotificationController {

    private final WechatPayApiV3Client client;
    private final WechatPayProperties properties;
    private final WechatPayNotificationPort notificationPort;
    private final TrustedInboxPort inbox;
    private final ObjectMapper mapper;

    public WechatPayNotificationController(
            WechatPayApiV3Client client,
            WechatPayProperties properties,
            WechatPayNotificationPort notificationPort,
            TrustedInboxPort inbox,
            ObjectMapper mapper) {
        this.client = client;
        this.properties = properties;
        this.notificationPort = notificationPort;
        this.inbox = inbox;
        this.mapper = mapper;
    }

    @PostMapping(
            value = "/api/v1/wechat-pay/notifications/native-payments",
            consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Void> nativePayment(
            @RequestHeader("Wechatpay-Serial") String serial,
            @RequestHeader("Wechatpay-Timestamp") String timestamp,
            @RequestHeader("Wechatpay-Nonce") String nonce,
            @RequestHeader("Wechatpay-Signature") String signature,
            @RequestBody String rawBody) {
        Notification notification = verifyAndDecrypt(
                serial, timestamp, nonce, signature, rawBody,
                "TRANSACTION.SUCCESS", "transaction");
        JsonNode resource = notification.resource();
        String mchid = requiredText(resource, "mchid");
        requireConfiguredMerchant(mchid);
        String outTradeNo = requiredText(resource, "out_trade_no");
        ObjectNode normalized = mapper.createObjectNode();
        copyText(resource, normalized, "appid");
        copyText(resource, normalized, "mchid");
        copyText(resource, normalized, "out_trade_no");
        copyText(resource, normalized, "transaction_id");
        copyText(resource, normalized, "trade_state");
        copyText(resource, normalized, "success_time");
        ObjectNode amount = normalized.putObject("amount");
        amount.put("total", requiredLong(resource.path("amount"), "total"));
        amount.put("payer_total",
                requiredLong(resource.path("amount"), "payer_total"));
        normalized.putObject("payer").put(
                "openid", requiredText(resource.path("payer"), "openid"));
        receive(notification, rawBody, serial,
                WechatPayNotificationPort.PAYMENT_KIND,
                mchid, outTradeNo, normalized);
        return ResponseEntity.noContent().build();
    }

    @PostMapping(
            value = "/api/v1/wechat-pay/notifications/merchant-transfers",
            consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Void> merchantTransfer(
            @RequestHeader("Wechatpay-Serial") String serial,
            @RequestHeader("Wechatpay-Timestamp") String timestamp,
            @RequestHeader("Wechatpay-Nonce") String nonce,
            @RequestHeader("Wechatpay-Signature") String signature,
            @RequestBody String rawBody) {
        Notification notification = verifyAndDecrypt(
                serial, timestamp, nonce, signature, rawBody,
                "MCHTRANSFER.BILL.FINISHED", "mch_payment");
        JsonNode resource = notification.resource();
        String mchid = requiredText(resource, "mch_id");
        requireConfiguredMerchant(mchid);
        String outBillNo = requiredText(resource, "out_bill_no");
        ObjectNode normalized = mapper.createObjectNode();
        copyText(resource, normalized, "out_bill_no");
        copyText(resource, normalized, "transfer_bill_no");
        copyText(resource, normalized, "state");
        copyText(resource, normalized, "mch_id");
        copyText(resource, normalized, "openid");
        copyText(resource, normalized, "update_time");
        String failReason = text(resource, "fail_reason");
        if (failReason != null) normalized.put("fail_reason", failReason);
        normalized.put("transfer_amount",
                requiredLong(resource, "transfer_amount"));
        receive(notification, rawBody, serial,
                WechatPayNotificationPort.TRANSFER_KIND,
                mchid, outBillNo, normalized);
        return ResponseEntity.noContent().build();
    }

    private Notification verifyAndDecrypt(
            String serial,
            String timestamp,
            String nonce,
            String signature,
            String rawBody,
            String expectedEventType,
            String expectedOriginalType) {
        client.verifyNotification(
                serial, timestamp, nonce, signature, rawBody);
        JsonNode envelope = mapper.readTree(rawBody);
        String id = requiredText(envelope, "id");
        if (!expectedEventType.equals(requiredText(envelope, "event_type"))) {
            throw new IllegalArgumentException(
                    "unexpected WeChat Pay notification event type");
        }
        JsonNode encrypted = envelope.path("resource");
        if (!expectedOriginalType.equals(
                requiredText(encrypted, "original_type"))) {
            throw new IllegalArgumentException(
                    "unexpected WeChat Pay notification resource type");
        }
        return new Notification(id, client.decryptResource(encrypted));
    }

    private void receive(
            Notification notification,
            String rawBody,
            String serial,
            String kind,
            String mchid,
            String externalOrderNo,
            ObjectNode normalized) {
        TrustedInboxReceipt receipt = inbox.receive(new TrustedInboxMessage(
                "wechat-pay", mchid, notification.id(), kind, 1,
                rawBody.getBytes(StandardCharsets.UTF_8),
                mapper.writeValueAsString(normalized),
                "API_V3_SIGNATURE", "platform-certificate:" + serial,
                UUID.randomUUID(), null, TrustedInboxExecutionLane.FUNDS,
                notificationPort.scopeResolver(
                        kind, mchid, externalOrderNo)));
        if (!receipt.transportAcknowledgementAllowed()) {
            throw new IllegalStateException(
                    "reliable inbox did not commit an ACK-safe receipt");
        }
    }

    private void requireConfiguredMerchant(String mchid) {
        if (!properties.getMchid().equals(mchid)) {
            throw new SecurityException(
                    "WeChat Pay notification merchant mismatch");
        }
    }

    @ExceptionHandler({SecurityException.class, IllegalArgumentException.class})
    public ResponseEntity<Map<String, String>> rejected(RuntimeException failure) {
        return ResponseEntity.badRequest()
                .contentType(MediaType.APPLICATION_JSON)
                .body(Map.of(
                        "code", "FAIL",
                        "message", "微信支付通知校验失败"));
    }

    private static void copyText(
            JsonNode source, ObjectNode target, String field) {
        target.put(field, requiredText(source, field));
    }

    private static String requiredText(JsonNode node, String field) {
        String value = text(node, field);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(
                    "WeChat Pay notification lacks " + field);
        }
        return value;
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        return value == null || value.isNull() ? null : value.asText();
    }

    private static long requiredLong(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.canConvertToLong()) {
            throw new IllegalArgumentException(
                    "WeChat Pay notification lacks " + field);
        }
        return value.asLong();
    }

    private record Notification(String id, JsonNode resource) {
    }
}
