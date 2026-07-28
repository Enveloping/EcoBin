package org.enveloping.ecobin.device.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.target.TargetDeviceApplication;
import org.enveloping.ecobin.device.web.v1.DeviceModels.CreateDeviceAssetRequest;
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

import java.net.URI;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/platform/device-assets")
public class PlatformDeviceAssetController {

    private final TargetDeviceApplication application;

    public PlatformDeviceAssetController(
            TargetDeviceApplication application) {
        this.application = application;
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<PageData<DeviceAssetView>>> list(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String hardwareSn,
            @RequestParam(required = false) String modelCode,
            @RequestParam(required = false) String productionBatch,
            @RequestParam(required = false) String lifecycleStatus,
            HttpServletRequest request) {
        return noStore(application.listAssets(
                page,
                pageSize,
                hardwareSn,
                modelCode,
                productionBatch,
                lifecycleStatus), request);
    }

    @PostMapping
    public ResponseEntity<TargetApiEnvelope<DeviceAssetView>> create(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody CreateDeviceAssetRequest body,
            HttpServletRequest request) {
        DeviceAssetView created =
                application.createAsset(operationUid, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/device-assets/"
                                + created.hardwareSn()))
                .cacheControl(CacheControl.noStore())
                .body(ok(created, request));
    }

    @GetMapping("/{hardwareSn}")
    public ResponseEntity<TargetApiEnvelope<DeviceAssetView>> detail(
            @PathVariable String hardwareSn,
            HttpServletRequest request) {
        return noStore(application.asset(hardwareSn), request);
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
