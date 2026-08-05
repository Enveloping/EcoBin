package org.enveloping.ecobin.operations.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.operations.application.statistics.OperationalOverviewService;
import org.enveloping.ecobin.operations.web.v1.OperationalOverviewModels.OperationalOverview;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;

@RestController
public class OperationalOverviewController {

    private final OperationalOverviewService service;

    public OperationalOverviewController(OperationalOverviewService service) {
        this.service = service;
    }

    @GetMapping("/api/v1/web/statistics/operational-overview")
    public ResponseEntity<TargetApiEnvelope<OperationalOverview>> overview(
            @RequestParam LocalDate businessDateFrom,
            @RequestParam LocalDate businessDateToExclusive,
            @RequestParam(required = false) String organizationCode,
            HttpServletRequest request) {
        return ok(service.web(false, null, organizationCode,
                businessDateFrom, businessDateToExclusive), request);
    }

    @GetMapping("/api/v1/web/platform/tenants/{tenantCode}"
            + "/statistics/operational-overview")
    public ResponseEntity<TargetApiEnvelope<OperationalOverview>>
    platformOverview(
            @PathVariable String tenantCode,
            @RequestParam LocalDate businessDateFrom,
            @RequestParam LocalDate businessDateToExclusive,
            @RequestParam(required = false) String organizationCode,
            HttpServletRequest request) {
        return ok(service.web(true, tenantCode, organizationCode,
                businessDateFrom, businessDateToExclusive), request);
    }

    @GetMapping("/api/v1/miniapp-staff/statistics/operational-overview")
    public ResponseEntity<TargetApiEnvelope<OperationalOverview>>
    staffOverview(
            @RequestParam LocalDate businessDateFrom,
            @RequestParam LocalDate businessDateToExclusive,
            HttpServletRequest request) {
        return ok(service.miniapp(
                businessDateFrom, businessDateToExclusive), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> ok(
            T value, HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        value, TargetRequestIds.resolve(request)));
    }
}
