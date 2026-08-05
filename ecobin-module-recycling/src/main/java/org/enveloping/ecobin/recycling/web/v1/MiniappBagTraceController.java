package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.clean.BagTraceQueryService;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagTraceDeliveryOrderDetail;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagTraceDeliveryOrderPage;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagUseCyclePage;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

@RestController
public class MiniappBagTraceController {

    private final BagTraceQueryService service;

    public MiniappBagTraceController(BagTraceQueryService service) {
        this.service = service;
    }

    @GetMapping("/api/v1/miniapp/bags/{bagQr}/use-cycles")
    public ResponseEntity<TargetApiEnvelope<BagUseCyclePage>> cycles(
            @PathVariable String bagQr,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(
                service.cycles(bagQr, cursor, limit),
                request);
    }

    @GetMapping("/api/v1/miniapp/bags/{bagQr}/use-cycles/{cycleUid}"
            + "/delivery-orders")
    public ResponseEntity<TargetApiEnvelope<BagTraceDeliveryOrderPage>> orders(
            @PathVariable String bagQr,
            @PathVariable UUID cycleUid,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(
                service.orders(bagQr, cycleUid, cursor, limit),
                request);
    }

    @GetMapping("/api/v1/miniapp/bags/{bagQr}/use-cycles/{cycleUid}"
            + "/delivery-orders/{deliveryOrderNo}")
    public ResponseEntity<TargetApiEnvelope<BagTraceDeliveryOrderDetail>> order(
            @PathVariable String bagQr,
            @PathVariable UUID cycleUid,
            @PathVariable String deliveryOrderNo,
            HttpServletRequest request) {
        return noStore(
                service.order(bagQr, cycleUid, deliveryOrderNo),
                request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> noStore(
            T value, HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        value, TargetRequestIds.resolve(request)));
    }
}
