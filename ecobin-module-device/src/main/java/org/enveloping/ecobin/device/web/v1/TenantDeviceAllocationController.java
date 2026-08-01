package org.enveloping.ecobin.device.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.device.application.target.DeviceLifecycleApplication;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.TenantAllocationView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.PageData;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/device-asset-allocations")
public class TenantDeviceAllocationController {

    private final DeviceLifecycleApplication application;

    public TenantDeviceAllocationController(
            DeviceLifecycleApplication application) {
        this.application = application;
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<PageData<TenantAllocationView>>>
            allocations(
                    @RequestParam(defaultValue = "1") int page,
                    @RequestParam(defaultValue = "20") int pageSize,
                    @RequestParam(required = false) String status,
                    @RequestParam(required = false) String hardwareSn,
                    HttpServletRequest request) {
        return noStore(application.allocations(
                false, null, page, pageSize, status, hardwareSn), request);
    }

    @GetMapping("/{allocationUid}")
    public ResponseEntity<TargetApiEnvelope<TenantAllocationView>> allocation(
            @PathVariable UUID allocationUid,
            HttpServletRequest request) {
        return noStore(application.allocation(
                false, allocationUid), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> noStore(
            T data,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }
}
