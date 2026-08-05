package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.fullness.FullnessQueryService;
import org.enveloping.ecobin.recycling.web.v1.FullnessModels.PortCapacityView;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class MiniappStaffFullnessController {

    private final FullnessQueryService service;

    public MiniappStaffFullnessController(FullnessQueryService service) {
        this.service = service;
    }

    @GetMapping("/api/v1/miniapp-staff/device-deployments/{deploymentCode}"
            + "/ports/{portNo}/capacity")
    public ResponseEntity<TargetApiEnvelope<PortCapacityView>> capacity(
            @PathVariable String deploymentCode,
            @PathVariable int portNo,
            HttpServletRequest request) {
        return noStore(
                service.staffCapacity(deploymentCode, portNo),
                request);
    }

    @GetMapping("/api/v1/miniapp-staff/device-deployments/{deploymentCode}"
            + "/ports/{portNo}/fullness-state/current")
    public ResponseEntity<TargetApiEnvelope<PortCapacityView>> current(
            @PathVariable String deploymentCode,
            @PathVariable int portNo,
            HttpServletRequest request) {
        return noStore(
                service.staffCapacity(deploymentCode, portNo),
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
