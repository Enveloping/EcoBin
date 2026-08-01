package org.enveloping.ecobin.device.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.target.DeviceLifecycleApplication;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.CreateTenantAllocationRequest;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.CredentialRotationConfirmationRequest;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.CredentialRotationConfirmationView;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.MaintenanceClearanceRequest;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.ReclaimTenantAllocationRequest;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.TenantAllocationView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.PageData;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.net.URI;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/platform")
public class PlatformDeviceLifecycleController {

    private final DeviceLifecycleApplication application;

    public PlatformDeviceLifecycleController(
            DeviceLifecycleApplication application) {
        this.application = application;
    }

    @GetMapping("/device-asset-allocations")
    public ResponseEntity<TargetApiEnvelope<PageData<TenantAllocationView>>>
            allocations(
                    @RequestParam(required = false) String tenantCode,
                    @RequestParam(defaultValue = "1") int page,
                    @RequestParam(defaultValue = "20") int pageSize,
                    @RequestParam(required = false) String status,
                    @RequestParam(required = false) String hardwareSn,
                    HttpServletRequest request) {
        return noStore(application.allocations(
                true, tenantCode, page, pageSize, status, hardwareSn), request);
    }

    @GetMapping("/device-asset-allocations/{allocationUid}")
    public ResponseEntity<TargetApiEnvelope<TenantAllocationView>> allocation(
            @PathVariable UUID allocationUid,
            HttpServletRequest request) {
        return noStore(application.allocation(
                true, allocationUid), request);
    }

    @PostMapping("/tenants/{tenantCode}/device-asset-allocations")
    public ResponseEntity<TargetApiEnvelope<TenantAllocationView>> allocate(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String tenantCode,
            @Valid @RequestBody CreateTenantAllocationRequest body,
            HttpServletRequest request) {
        TenantAllocationView created = application.allocate(
                operationUid, tenantCode, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/device-asset-allocations/"
                                + created.allocationUid()))
                .cacheControl(CacheControl.noStore())
                .body(ok(created, request));
    }

    @PostMapping("/device-asset-allocations/{allocationUid}/reclaims")
    public TargetApiEnvelope<TenantAllocationView> reclaim(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable UUID allocationUid,
            @Valid @RequestBody ReclaimTenantAllocationRequest body,
            HttpServletRequest request) {
        return ok(application.reclaim(
                operationUid, allocationUid, body), request);
    }

    @PostMapping("/device-assets/{hardwareSn}"
            + "/onenet-credential-rotation-confirmations")
    public TargetApiEnvelope<CredentialRotationConfirmationView>
            confirmCredentialRotation(
                    @RequestHeader("Idempotency-Key") UUID operationUid,
                    @PathVariable String hardwareSn,
                    @Valid @RequestBody
                    CredentialRotationConfirmationRequest body,
                    HttpServletRequest request) {
        return ok(application.confirmCredentialRotation(
                operationUid, hardwareSn, body), request);
    }

    @PostMapping("/device-assets/{hardwareSn}/maintenance-clearances")
    public TargetApiEnvelope<TenantAllocationView> clearMaintenance(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String hardwareSn,
            @Valid @RequestBody MaintenanceClearanceRequest body,
            HttpServletRequest request) {
        return ok(application.clearMaintenance(
                operationUid, hardwareSn, body), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> noStore(
            T data,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(ok(data, request));
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                data, TargetRequestIds.resolve(request));
    }
}
