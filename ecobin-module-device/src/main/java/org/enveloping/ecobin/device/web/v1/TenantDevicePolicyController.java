package org.enveloping.ecobin.device.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.target.TargetDeviceApplication;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DevicePolicyReleaseRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DevicePolicyView;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/device-configuration-policy")
public class TenantDevicePolicyController {

    private final TargetDeviceApplication application;

    public TenantDevicePolicyController(
            TargetDeviceApplication application) {
        this.application = application;
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<DevicePolicyView>> get(
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(ok(application.devicePolicy(false), request));
    }

    @PostMapping("/releases")
    public ResponseEntity<TargetApiEnvelope<DevicePolicyView>> release(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody DevicePolicyReleaseRequest body,
            HttpServletRequest request) {
        return ResponseEntity.accepted()
                .cacheControl(CacheControl.noStore())
                .body(ok(application.releaseDevicePolicy(
                        false, operationUid, body), request));
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data, HttpServletRequest request) {
        return TargetApiEnvelope.ok(data, TargetRequestIds.resolve(request));
    }
}
