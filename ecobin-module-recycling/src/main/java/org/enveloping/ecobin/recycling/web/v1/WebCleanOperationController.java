package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.clean.CleanOperationQueryService;
import org.enveloping.ecobin.recycling.web.v1.CleanOperationModels.WebCleanOperationDetail;
import org.enveloping.ecobin.recycling.web.v1.CleanOperationModels.WebCleanOperationItem;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.CursorPage;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.UUID;

@RestController
public class WebCleanOperationController {

    private final CleanOperationQueryService queryService;

    public WebCleanOperationController(
            CleanOperationQueryService queryService) {
        this.queryService = queryService;
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/clean-operations")
    public TargetApiEnvelope<CursorPage<WebCleanOperationItem>>
    staffOperations(
            @PathVariable String organizationCode,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) UUID cleanerUserUid,
            @RequestParam(required = false) String deviceCode,
            @RequestParam(required = false) Integer portNo,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant createdFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant createdTo,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.webOperations(
                        false,
                        null,
                        organizationCode,
                        cursor,
                        limit,
                        status,
                        cleanerUserUid,
                        deviceCode,
                        portNo,
                        createdFrom,
                        createdTo),
                TargetRequestIds.resolve(request));
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/clean-operations")
    public TargetApiEnvelope<CursorPage<WebCleanOperationItem>>
    platformOperations(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) UUID cleanerUserUid,
            @RequestParam(required = false) String deviceCode,
            @RequestParam(required = false) Integer portNo,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant createdFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant createdTo,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.webOperations(
                        true,
                        tenantCode,
                        organizationCode,
                        cursor,
                        limit,
                        status,
                        cleanerUserUid,
                        deviceCode,
                        portNo,
                        createdFrom,
                        createdTo),
                TargetRequestIds.resolve(request));
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/clean-operations/{operationUid}")
    public TargetApiEnvelope<WebCleanOperationDetail> staffOperation(
            @PathVariable String organizationCode,
            @PathVariable UUID operationUid,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.webOperation(
                        false, null, organizationCode, operationUid),
                TargetRequestIds.resolve(request));
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/clean-operations/{operationUid}")
    public TargetApiEnvelope<WebCleanOperationDetail> platformOperation(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable UUID operationUid,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.webOperation(
                        true,
                        tenantCode,
                        organizationCode,
                        operationUid),
                TargetRequestIds.resolve(request));
    }
}
