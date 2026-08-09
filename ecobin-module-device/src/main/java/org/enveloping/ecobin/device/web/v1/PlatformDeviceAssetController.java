package org.enveloping.ecobin.device.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.target.TargetDeviceApplication;
import org.enveloping.ecobin.device.web.v1.DeviceModels.AcceptanceEvidenceView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.AssignTenantRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.CreateDeviceAssetRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationAcceptedView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationApplicationView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationResynchronizationRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationRollForwardRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationVersionSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationVersionView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.CursorPage;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeviceAssetView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeviceControlRequest;
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
import java.util.List;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/platform/device-assets")
public class PlatformDeviceAssetController {

    private final TargetDeviceApplication application;

    public PlatformDeviceAssetController(TargetDeviceApplication application) {
        this.application = application;
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<PageData<DeviceAssetView>>> list(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String hardwareSn,
            @RequestParam(required = false) String lifecycleStatus,
            @RequestParam(required = false) String acceptanceStatus,
            HttpServletRequest request) {
        return noStore(application.listPlatformAssets(
                page, pageSize, hardwareSn, lifecycleStatus,
                acceptanceStatus), request);
    }

    @PostMapping
    public ResponseEntity<TargetApiEnvelope<DeviceAssetView>> create(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody CreateDeviceAssetRequest body,
            HttpServletRequest request) {
        DeviceAssetView created = application.createAsset(operationUid, body);
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
        return noStore(application.platformAsset(hardwareSn), request);
    }

    @PostMapping("/{hardwareSn}/tenant-assignments")
    public TargetApiEnvelope<DeviceAssetView> assignTenant(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String hardwareSn,
            @Valid @RequestBody AssignTenantRequest body,
            HttpServletRequest request) {
        return ok(application.assignTenant(
                operationUid, hardwareSn, body), request);
    }

    @PostMapping("/{hardwareSn}/acceptance-evaluations")
    public TargetApiEnvelope<DeviceAssetView> reevaluateAcceptance(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String hardwareSn,
            HttpServletRequest request) {
        return ok(application.reevaluateAcceptance(
                operationUid, hardwareSn), request);
    }

    @GetMapping("/{hardwareSn}/acceptance-evidence")
    public ResponseEntity<TargetApiEnvelope<List<AcceptanceEvidenceView>>>
            acceptanceEvidence(
                    @PathVariable String hardwareSn,
                    HttpServletRequest request) {
        return noStore(application.acceptanceEvidence(hardwareSn), request);
    }

    @GetMapping("/{hardwareSn}/configuration-versions")
    public ResponseEntity<TargetApiEnvelope<
            CursorPage<ConfigurationVersionSummary>>> configurationVersions(
                    @PathVariable String hardwareSn,
                    @RequestParam(required = false) Long beforeVersionNo,
                    @RequestParam(defaultValue = "20") int limit,
                    HttpServletRequest request) {
        return noStore(application.platformConfigurationVersions(
                hardwareSn, beforeVersionNo, limit), request);
    }

    @GetMapping("/{hardwareSn}/configuration-versions/{versionNo}")
    public ResponseEntity<TargetApiEnvelope<ConfigurationVersionView>>
            configurationVersion(
                    @PathVariable String hardwareSn,
                    @PathVariable long versionNo,
                    HttpServletRequest request) {
        return noStore(application.platformConfigurationVersion(
                hardwareSn, versionNo), request);
    }

    @PostMapping("/{hardwareSn}/configuration-roll-forwards")
    public ResponseEntity<TargetApiEnvelope<ConfigurationAcceptedView>>
            rollForwardConfiguration(
                    @RequestHeader("Idempotency-Key") UUID operationUid,
                    @PathVariable String hardwareSn,
                    @Valid @RequestBody ConfigurationRollForwardRequest body,
                    HttpServletRequest request) {
        return ResponseEntity.accepted()
                .cacheControl(CacheControl.noStore())
                .body(ok(application.rollForwardPlatformConfiguration(
                        operationUid, hardwareSn, body), request));
    }

    @GetMapping("/{hardwareSn}/configuration-applications/{applicationUid}")
    public ResponseEntity<TargetApiEnvelope<ConfigurationApplicationView>>
            configurationApplication(
                    @PathVariable String hardwareSn,
                    @PathVariable UUID applicationUid,
                    HttpServletRequest request) {
        return noStore(application.platformConfigurationApplication(
                hardwareSn, applicationUid), request);
    }

    @PostMapping("/{hardwareSn}/configuration-applications/"
            + "{applicationUid}/resynchronizations")
    public ResponseEntity<TargetApiEnvelope<ConfigurationAcceptedView>>
            resynchronizeConfiguration(
                    @RequestHeader("Idempotency-Key") UUID operationUid,
                    @PathVariable String hardwareSn,
                    @PathVariable UUID applicationUid,
                    @Valid @RequestBody
                    ConfigurationResynchronizationRequest body,
                    HttpServletRequest request) {
        return ResponseEntity.accepted()
                .cacheControl(CacheControl.noStore())
                .body(ok(application.resynchronizePlatformConfiguration(
                        operationUid,
                        hardwareSn,
                        applicationUid,
                        body), request));
    }

    @PostMapping("/{hardwareSn}/disablements")
    public TargetApiEnvelope<DeviceAssetView> disable(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String hardwareSn,
            @Valid @RequestBody DeviceControlRequest body,
            HttpServletRequest request) {
        return ok(application.disable(
                operationUid, hardwareSn, body), request);
    }

    @PostMapping("/{hardwareSn}/restorations")
    public TargetApiEnvelope<DeviceAssetView> restore(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String hardwareSn,
            @Valid @RequestBody DeviceControlRequest body,
            HttpServletRequest request) {
        return ok(application.restore(
                operationUid, hardwareSn, body), request);
    }

    @PostMapping("/{hardwareSn}/retirements")
    public TargetApiEnvelope<DeviceAssetView> retire(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String hardwareSn,
            @Valid @RequestBody DeviceControlRequest body,
            HttpServletRequest request) {
        return ok(application.retire(
                operationUid, hardwareSn, body), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> noStore(
            T data, HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(ok(data, request));
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data, HttpServletRequest request) {
        return TargetApiEnvelope.ok(data, TargetRequestIds.resolve(request));
    }
}
