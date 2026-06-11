package org.enveloping.ecobin.business.onenet;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.business.dto.CleanGrossRequest;
import org.enveloping.ecobin.business.dto.CleanTareRequest;
import org.enveloping.ecobin.business.dto.DeliveryReportRequest;
import org.enveloping.ecobin.business.service.CleanOrderService;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.framework.onenet.OneNetMessageHandler;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;

/**
 * OneNet 北向消息分发器（设备 → 后端上行的业务侧实现）。
 * <p>
 * 解析第二层明文 {@code {msgType, subData}}：设备身份取 {@code subData.deviceName}（= {@code biz_device.sn}，
 * 物模型 §0「身份不进 payload」由报文头补回）。{@code thingEvent} 的业务事件在 {@code subData.params} 中
 * 按 identifier 承载，逐一映射到现有 Service（复用与明文 SN 直连端点同一批入口，物模型 §1）。
 * <p>
 * 事件输出的精确结构（{@code params.<id>} 是否含 {@code value} 包裹）以联调时 {@code OneNetMqConsumer}
 * 打印的真实报文为准，本类做了「有 value 用 value、否则用本节点」的兼容解析。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class OneNetEventDispatcher implements OneNetMessageHandler {

    private final CleanOrderService cleanOrderService;
    private final DeliveryOrderService deliveryOrderService;
    private final ObjectMapper objectMapper;

    // 物模型事件标识符（与 docs/onenet-thing-model.md §4 对齐）
    private static final String EVT_CLEAN_GROSS = "cleanGross";
    private static final String EVT_CLEAN_TARE = "cleanTare";
    private static final String EVT_DELIVERY_COMPLETE = "deliveryComplete";

    @Override
    public void handle(String decryptedJson) {
        JsonNode root = objectMapper.readTree(decryptedJson);
        String msgType = root.path("msgType").asString();
        JsonNode subData = root.path("subData");
        String sn = subData.path("deviceName").asString();

        if (sn == null || sn.isBlank()) {
            log.warn("[OneNet·分发] 报文缺少 deviceName(sn)，跳过：msgType={}", msgType);
            return;
        }

        switch (msgType) {
            case "thingEvent" -> dispatchEvents(sn, subData.path("params"));
            case "thingProperty" -> log.info("[OneNet·分发] 收到属性上报（设备状态 Service 待补，暂跳过）sn={}", sn);
            case "deviceOnline", "deviceOffline" ->
                    log.info("[OneNet·分发] 收到上下线 {} sn={}（在线状态 Service 待补，暂跳过）", msgType, sn);
            case "thingServiceReply" ->
                    log.info("[OneNet·分发] 收到服务调用回执 sn={}（下发回执处理待补，暂跳过）", sn);
            default -> log.info("[OneNet·分发] 未识别 msgType={} sn={}，跳过", msgType, sn);
        }
    }

    /** 遍历 thingEvent 的 params，按 identifier 路由到对应业务入口。 */
    private void dispatchEvents(String sn, JsonNode params) {
        if (params == null || !params.isObject()) {
            log.warn("[OneNet·分发] thingEvent 缺少 params，sn={}", sn);
            return;
        }
        for (String identifier : params.propertyNames()) {
            JsonNode value = unwrap(params.get(identifier));
            try {
                switch (identifier) {
                    case EVT_CLEAN_GROSS -> handleCleanGross(sn, value);
                    case EVT_CLEAN_TARE -> handleCleanTare(sn, value);
                    case EVT_DELIVERY_COMPLETE -> handleDeliveryComplete(sn, value);
                    default -> log.info("[OneNet·分发] 未处理事件 identifier={} sn={}（如告警，Service 待补）",
                            identifier, sn);
                }
            } catch (Exception e) {
                log.error("[OneNet·分发] 处理事件 {} 失败 sn={}（已忽略）", identifier, sn, e);
            }
        }
    }

    private void handleCleanGross(String sn, JsonNode v) {
        CleanGrossRequest req = new CleanGrossRequest();
        req.setSn(sn);
        req.setCleanOrderId(v.path("cleanOrderId").asLong());
        req.setWeight(decimal(v, "weight"));
        cleanOrderService.reportGross(req);
        log.info("[OneNet·分发] cleanGross 已入账 sn={}, cleanOrderId={}", sn, req.getCleanOrderId());
    }

    private void handleCleanTare(String sn, JsonNode v) {
        CleanTareRequest req = new CleanTareRequest();
        req.setSn(sn);
        req.setCleanOrderId(v.path("cleanOrderId").asLong());
        req.setWeight(decimal(v, "weight"));
        cleanOrderService.reportTare(req);
        log.info("[OneNet·分发] cleanTare 已入账 sn={}, cleanOrderId={}", sn, req.getCleanOrderId());
    }

    private void handleDeliveryComplete(String sn, JsonNode v) {
        DeliveryReportRequest req = new DeliveryReportRequest();
        req.setSn(sn);
        req.setDeliveryToken(v.path("deliveryToken").asString());
        req.setWeight(decimal(v, "weight"));
        if (v.has("wasteType1")) {
            req.setWasteType1(v.path("wasteType1").asInt());
        }
        if (v.has("wasteType2")) {
            req.setWasteType2(v.path("wasteType2").asInt());
        }
        deliveryOrderService.completeDelivery(req);
        log.info("[OneNet·分发] deliveryComplete 已入账 sn={}, deliveryToken={}", sn, req.getDeliveryToken());
    }

    /** OneJSON 事件输出可能被 {@code {"value":{..},"time":..}} 包裹，兼容解出真正的字段对象。 */
    private static JsonNode unwrap(JsonNode node) {
        if (node != null && node.isObject() && node.has("value") && node.get("value").isObject()) {
            return node.get("value");
        }
        return node;
    }

    private static BigDecimal decimal(JsonNode v, String field) {
        return new BigDecimal(v.path(field).asString());
    }
}
