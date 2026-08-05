package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.clean.WebBagTraceQueryService;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagCleanRecordPage;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagOccupancyEventPage;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.WebBagDeliveryOrderPage;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.WebBagDetail;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WebBagTraceController {

    private static final String STAFF_BASE =
            "/api/v1/web/organizations/{organizationCode}/bags/{bagQr}";
    private static final String PLATFORM_BASE =
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}/bags/{bagQr}";

    private final WebBagTraceQueryService service;

    public WebBagTraceController(WebBagTraceQueryService service) {
        this.service = service;
    }

    @GetMapping(STAFF_BASE)
    public ResponseEntity<TargetApiEnvelope<WebBagDetail>> detail(
            @PathVariable String organizationCode,
            @PathVariable String bagQr,
            HttpServletRequest request) {
        return noStore(service.detail(
                false, null, organizationCode, bagQr), request);
    }

    @GetMapping(PLATFORM_BASE)
    public ResponseEntity<TargetApiEnvelope<WebBagDetail>> platformDetail(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String bagQr,
            HttpServletRequest request) {
        return noStore(service.detail(
                true, tenantCode, organizationCode, bagQr), request);
    }

    @GetMapping(STAFF_BASE + "/occupancy-events")
    public ResponseEntity<TargetApiEnvelope<BagOccupancyEventPage>> events(
            @PathVariable String organizationCode,
            @PathVariable String bagQr,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(service.events(
                false, null, organizationCode, bagQr, cursor, limit), request);
    }

    @GetMapping(PLATFORM_BASE + "/occupancy-events")
    public ResponseEntity<TargetApiEnvelope<BagOccupancyEventPage>> platformEvents(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String bagQr,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(service.events(
                true, tenantCode, organizationCode, bagQr, cursor, limit),
                request);
    }

    @GetMapping(STAFF_BASE + "/clean-records")
    public ResponseEntity<TargetApiEnvelope<BagCleanRecordPage>> cleanRecords(
            @PathVariable String organizationCode,
            @PathVariable String bagQr,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(service.cleanRecords(
                false, null, organizationCode, bagQr, cursor, limit), request);
    }

    @GetMapping(PLATFORM_BASE + "/clean-records")
    public ResponseEntity<TargetApiEnvelope<BagCleanRecordPage>> platformCleanRecords(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String bagQr,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(service.cleanRecords(
                true, tenantCode, organizationCode, bagQr, cursor, limit),
                request);
    }

    @GetMapping(STAFF_BASE + "/delivery-orders")
    public ResponseEntity<TargetApiEnvelope<WebBagDeliveryOrderPage>> deliveryOrders(
            @PathVariable String organizationCode,
            @PathVariable String bagQr,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(service.deliveryOrders(
                false, null, organizationCode, bagQr, cursor, limit), request);
    }

    @GetMapping(PLATFORM_BASE + "/delivery-orders")
    public ResponseEntity<TargetApiEnvelope<WebBagDeliveryOrderPage>> platformDeliveryOrders(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String bagQr,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(service.deliveryOrders(
                true, tenantCode, organizationCode, bagQr, cursor, limit),
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
