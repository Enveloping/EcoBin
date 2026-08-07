package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.fullness.FullnessQueryService;
import org.enveloping.ecobin.recycling.web.v1.FullnessModels.FullnessStateChangeItem;
import org.enveloping.ecobin.recycling.web.v1.FullnessModels.FullnessStateChangePage;
import org.enveloping.ecobin.recycling.web.v1.FullnessModels.PortCapacityView;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

@RestController
public class WebFullnessController {

    private static final String STAFF_BASE =
            "/api/v1/web/organizations/{organizationCode}"
                    + "/devices/{deviceCode}"
                    + "/ports/{portNo}";
    private static final String PLATFORM_BASE =
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/devices/{deviceCode}"
                    + "/ports/{portNo}";

    private final FullnessQueryService service;

    public WebFullnessController(FullnessQueryService service) {
        this.service = service;
    }

    @GetMapping(STAFF_BASE + "/capacity")
    public ResponseEntity<TargetApiEnvelope<PortCapacityView>> capacity(
            @PathVariable String organizationCode,
            @PathVariable String deviceCode,
            @PathVariable int portNo,
            HttpServletRequest request) {
        return noStore(
                service.webCapacity(false, null, organizationCode,
                        deviceCode, portNo),
                request);
    }

    @GetMapping(PLATFORM_BASE + "/capacity")
    public ResponseEntity<TargetApiEnvelope<PortCapacityView>> platformCapacity(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deviceCode,
            @PathVariable int portNo,
            HttpServletRequest request) {
        return noStore(
                service.webCapacity(true, tenantCode, organizationCode,
                        deviceCode, portNo),
                request);
    }

    @GetMapping(STAFF_BASE + "/fullness-state-changes")
    public ResponseEntity<TargetApiEnvelope<FullnessStateChangePage>> history(
            @PathVariable String organizationCode,
            @PathVariable String deviceCode,
            @PathVariable int portNo,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(
                service.webHistory(false, null, organizationCode,
                        deviceCode, portNo, cursor, limit),
                request);
    }

    @GetMapping(PLATFORM_BASE + "/fullness-state-changes")
    public ResponseEntity<TargetApiEnvelope<FullnessStateChangePage>> platformHistory(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deviceCode,
            @PathVariable int portNo,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(
                service.webHistory(true, tenantCode, organizationCode,
                        deviceCode, portNo, cursor, limit),
                request);
    }

    @GetMapping(STAFF_BASE
            + "/fullness-state-changes/{stateChangeUid}")
    public ResponseEntity<TargetApiEnvelope<FullnessStateChangeItem>> stateChange(
            @PathVariable String organizationCode,
            @PathVariable String deviceCode,
            @PathVariable int portNo,
            @PathVariable UUID stateChangeUid,
            HttpServletRequest request) {
        return noStore(
                service.webStateChange(false, null, organizationCode,
                        deviceCode, portNo, stateChangeUid),
                request);
    }

    @GetMapping(PLATFORM_BASE
            + "/fullness-state-changes/{stateChangeUid}")
    public ResponseEntity<TargetApiEnvelope<FullnessStateChangeItem>> platformStateChange(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deviceCode,
            @PathVariable int portNo,
            @PathVariable UUID stateChangeUid,
            HttpServletRequest request) {
        return noStore(
                service.webStateChange(true, tenantCode, organizationCode,
                        deviceCode, portNo, stateChangeUid),
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
