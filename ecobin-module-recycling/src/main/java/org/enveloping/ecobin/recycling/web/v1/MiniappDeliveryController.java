package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.delivery.StartDeliverySessionService;
import org.enveloping.ecobin.recycling.application.deliveryorder.DeliveryOrderQueryService;
import org.enveloping.ecobin.recycling.application.deliveryquery.MiniappDeliveryQueryService;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.CursorPage;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.MiniappDeliveryOrderDetail;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.MiniappDeliveryOrderItem;
import org.enveloping.ecobin.recycling.web.v1.DeliveryModels.DeliveryOptionsView;
import org.enveloping.ecobin.recycling.web.v1.DeliveryModels.DeliverySessionAccepted;
import org.enveloping.ecobin.recycling.web.v1.DeliveryModels.DeliverySessionView;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.net.URI;
import java.util.UUID;

/**
 * 普通用户投递的 HTTP 入口。
 *
 * <p>查询接口返回当前展示快照；开始接口只受理一次投递意图并返回 202，设备是否执行、
 * 是否形成订单要由调用方通过会话查询继续观察。控制器不接收 tenantId、organizationId
 * 或 userId，真实作用域始终来自已认证的小程序会话。</p>
 */
@RestController
public class MiniappDeliveryController {

    private final StartDeliverySessionService startService;
    private final MiniappDeliveryQueryService queryService;
    private final DeliveryOrderQueryService orderQueryService;

    public MiniappDeliveryController(
            StartDeliverySessionService startService,
            MiniappDeliveryQueryService queryService,
            DeliveryOrderQueryService orderQueryService) {
        this.startService = startService;
        this.queryService = queryService;
        this.orderQueryService = orderQueryService;
    }

    @GetMapping(
            "/api/v1/miniapp/devices/{deviceCode}"
                    + "/delivery-options")
    public TargetApiEnvelope<DeliveryOptionsView> deliveryOptions(
            @PathVariable String deviceCode,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.deliveryOptions(deviceCode),
                TargetRequestIds.resolve(request));
    }

    @GetMapping("/api/v1/miniapp/delivery-sessions/{sessionUid}")
    public TargetApiEnvelope<DeliverySessionView> deliverySession(
            @PathVariable UUID sessionUid,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.deliverySession(sessionUid),
                TargetRequestIds.resolve(request));
    }

    @GetMapping("/api/v1/miniapp/me/delivery-orders")
    public TargetApiEnvelope<CursorPage<MiniappDeliveryOrderItem>>
    deliveryOrders(
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            @RequestParam(required = false) String reviewStatus,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                orderQueryService.miniappOrders(
                        cursor,
                        limit,
                        reviewStatus),
                TargetRequestIds.resolve(request));
    }

    @GetMapping(
            "/api/v1/miniapp/me/delivery-orders/{deliveryOrderNo}")
    public TargetApiEnvelope<MiniappDeliveryOrderDetail> deliveryOrder(
            @PathVariable String deliveryOrderNo,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                orderQueryService.miniappOrder(deliveryOrderNo),
                TargetRequestIds.resolve(request));
    }

    /**
     * Idempotency-Key 表示同一次用户意图；网络超时重试时必须复用原值。
     * Location 指向后续状态资源，不能把本响应理解为“门已经打开”。
     */
    @PostMapping(
            "/api/v1/miniapp/devices/{deviceCode}"
                    + "/ports/{portNo}/delivery-sessions")
    public ResponseEntity<TargetApiEnvelope<DeliverySessionAccepted>> start(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String deviceCode,
            @PathVariable int portNo,
            HttpServletRequest request) {
        DeliverySessionAccepted accepted = startService.start(
                operationUid,
                deviceCode,
                portNo);
        return ResponseEntity.accepted()
                .location(URI.create(accepted.statusUrl()))
                .body(TargetApiEnvelope.ok(
                        accepted,
                        TargetRequestIds.resolve(request)));
    }
}
