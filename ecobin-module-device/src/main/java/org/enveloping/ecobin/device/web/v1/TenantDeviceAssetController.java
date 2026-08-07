package org.enveloping.ecobin.device.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.target.TargetDeviceApplication;
import org.enveloping.ecobin.device.web.v1.DeviceModels.AssignOrganizationRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeviceAssetView;
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

import java.util.UUID;

/** 租户只看到仍处于 NORMAL 的永久资产，不展示部署进度。 */
@RestController
@RequestMapping("/api/v1/web/device-assets")
public class TenantDeviceAssetController {

    private final TargetDeviceApplication application;

    public TenantDeviceAssetController(TargetDeviceApplication application) {
        this.application = application;
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<PageData<DeviceAssetView>>> list(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String hardwareSn,
            HttpServletRequest request) {
        return noStore(application.listTenantAssets(
                page, pageSize, hardwareSn), request);
    }

    @GetMapping("/{hardwareSn}")
    public ResponseEntity<TargetApiEnvelope<DeviceAssetView>> detail(
            @PathVariable String hardwareSn,
            HttpServletRequest request) {
        return noStore(application.tenantAsset(hardwareSn), request);
    }

    @PostMapping("/{hardwareSn}/organization-assignments")
    public TargetApiEnvelope<DeviceAssetView> assignOrganization(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String hardwareSn,
            @Valid @RequestBody AssignOrganizationRequest body,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                application.assignOrganization(
                        operationUid, hardwareSn, body),
                TargetRequestIds.resolve(request));
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> noStore(
            T data, HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }
}
