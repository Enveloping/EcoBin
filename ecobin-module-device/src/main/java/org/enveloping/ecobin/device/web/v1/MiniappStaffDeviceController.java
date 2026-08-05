package org.enveloping.ecobin.device.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.device.application.target.MiniappStaffDeviceQueryService;
import org.enveloping.ecobin.device.web.v1.MiniappStaffDeviceModels.DeploymentDetail;
import org.enveloping.ecobin.device.web.v1.MiniappStaffDeviceModels.DeploymentSummary;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
public class MiniappStaffDeviceController {

    private final MiniappStaffDeviceQueryService service;

    public MiniappStaffDeviceController(
            MiniappStaffDeviceQueryService service) {
        this.service = service;
    }

    @GetMapping("/api/v1/miniapp-staff/device-deployments")
    public ResponseEntity<TargetApiEnvelope<List<DeploymentSummary>>> list(
            HttpServletRequest request) {
        return noStore(service.deployments(), request);
    }

    @GetMapping("/api/v1/miniapp-staff/device-deployments/{deploymentCode}")
    public ResponseEntity<TargetApiEnvelope<DeploymentDetail>> detail(
            @PathVariable String deploymentCode,
            HttpServletRequest request) {
        return noStore(service.deployment(deploymentCode), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> noStore(
            T data, HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }
}
