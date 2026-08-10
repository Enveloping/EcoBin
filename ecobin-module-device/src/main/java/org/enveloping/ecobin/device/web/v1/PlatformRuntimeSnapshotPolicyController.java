package org.enveloping.ecobin.device.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.target.TargetDeviceApplication;
import org.enveloping.ecobin.device.web.v1.DeviceModels.RuntimeSnapshotPolicyReleaseRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.RuntimeSnapshotPolicyView;
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
@RequestMapping("/api/v1/web/platform/device-runtime-snapshot-policy")
public class PlatformRuntimeSnapshotPolicyController {

    private final TargetDeviceApplication application;

    public PlatformRuntimeSnapshotPolicyController(
            TargetDeviceApplication application) {
        this.application = application;
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<RuntimeSnapshotPolicyView>> get(
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(ok(application.runtimeSnapshotPolicy(), request));
    }

    @PostMapping("/releases")
    public ResponseEntity<TargetApiEnvelope<RuntimeSnapshotPolicyView>> release(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody RuntimeSnapshotPolicyReleaseRequest body,
            HttpServletRequest request) {
        return ResponseEntity.accepted()
                .cacheControl(CacheControl.noStore())
                .body(ok(application.releaseRuntimeSnapshotPolicy(
                        operationUid, body), request));
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data, HttpServletRequest request) {
        return TargetApiEnvelope.ok(data, TargetRequestIds.resolve(request));
    }
}
