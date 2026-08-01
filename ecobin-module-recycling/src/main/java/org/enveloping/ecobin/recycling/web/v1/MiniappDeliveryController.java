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
            "/api/v1/miniapp/device-deployments/{deploymentCode}"
                    + "/delivery-options")
    public TargetApiEnvelope<DeliveryOptionsView> deliveryOptions(
            @PathVariable String deploymentCode,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.deliveryOptions(deploymentCode),
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

    @PostMapping(
            "/api/v1/miniapp/device-deployments/{deploymentCode}"
                    + "/ports/{portNo}/delivery-sessions")
    public ResponseEntity<TargetApiEnvelope<DeliverySessionAccepted>> start(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String deploymentCode,
            @PathVariable int portNo,
            HttpServletRequest request) {
        DeliverySessionAccepted accepted = startService.start(
                operationUid,
                deploymentCode,
                portNo);
        return ResponseEntity.accepted()
                .location(URI.create(accepted.statusUrl()))
                .body(TargetApiEnvelope.ok(
                        accepted,
                        TargetRequestIds.resolve(request)));
    }
}
