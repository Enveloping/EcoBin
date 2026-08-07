package org.enveloping.ecobin.device.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.target.TargetDeviceApplication;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationAcceptedView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationApplicationView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationReleaseRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationResynchronizationRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationVersionSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationVersionView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.CursorPage;
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

/** 机构只有查看和日常配置入口，没有部署、验收或激活操作。 */
@RestController
@RequestMapping("/api/v1/web/organizations/{organizationCode}/devices")
public class OrganizationDeviceController {

    private final TargetDeviceApplication application;

    public OrganizationDeviceController(TargetDeviceApplication application) {
        this.application = application;
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<PageData<DeviceAssetView>>> list(
            @PathVariable String organizationCode,
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String hardwareSn,
            HttpServletRequest request) {
        return noStore(application.listOrganizationAssets(
                organizationCode, page, pageSize, hardwareSn), request);
    }

    @GetMapping("/{deviceCode}")
    public ResponseEntity<TargetApiEnvelope<DeviceAssetView>> detail(
            @PathVariable String organizationCode,
            @PathVariable String deviceCode,
            HttpServletRequest request) {
        return noStore(application.organizationAsset(
                organizationCode, deviceCode), request);
    }

    @GetMapping("/{deviceCode}/configuration-versions")
    public ResponseEntity<TargetApiEnvelope<
            CursorPage<ConfigurationVersionSummary>>> configurationVersions(
                    @PathVariable String organizationCode,
                    @PathVariable String deviceCode,
                    @RequestParam(required = false) Long beforeVersionNo,
                    @RequestParam(defaultValue = "20") int limit,
                    HttpServletRequest request) {
        return noStore(application.configurationVersions(
                organizationCode,
                deviceCode,
                beforeVersionNo,
                limit), request);
    }

    @GetMapping("/{deviceCode}/configuration-versions/{versionNo}")
    public ResponseEntity<TargetApiEnvelope<ConfigurationVersionView>>
            configurationVersion(
                    @PathVariable String organizationCode,
                    @PathVariable String deviceCode,
                    @PathVariable long versionNo,
                    HttpServletRequest request) {
        return noStore(application.configurationVersion(
                organizationCode, deviceCode, versionNo), request);
    }

    @PostMapping("/{deviceCode}/configuration-releases")
    public ResponseEntity<TargetApiEnvelope<ConfigurationAcceptedView>>
            releaseConfiguration(
                    @RequestHeader("Idempotency-Key") UUID operationUid,
                    @PathVariable String organizationCode,
                    @PathVariable String deviceCode,
                    @Valid @RequestBody ConfigurationReleaseRequest body,
                    HttpServletRequest request) {
        ConfigurationAcceptedView accepted =
                application.releaseConfiguration(
                        operationUid,
                        organizationCode,
                        deviceCode,
                        body);
        return ResponseEntity.accepted()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        accepted, TargetRequestIds.resolve(request)));
    }

    @GetMapping("/{deviceCode}/configuration-applications/{applicationUid}")
    public ResponseEntity<TargetApiEnvelope<ConfigurationApplicationView>>
            configurationApplication(
                    @PathVariable String organizationCode,
                    @PathVariable String deviceCode,
                    @PathVariable UUID applicationUid,
                    HttpServletRequest request) {
        return noStore(application.configurationApplication(
                organizationCode, deviceCode, applicationUid), request);
    }

    @PostMapping("/{deviceCode}/configuration-applications/"
            + "{applicationUid}/resynchronizations")
    public ResponseEntity<TargetApiEnvelope<ConfigurationAcceptedView>>
            resynchronizeConfiguration(
                    @RequestHeader("Idempotency-Key") UUID operationUid,
                    @PathVariable String organizationCode,
                    @PathVariable String deviceCode,
                    @PathVariable UUID applicationUid,
                    @Valid @RequestBody
                    ConfigurationResynchronizationRequest body,
                    HttpServletRequest request) {
        return ResponseEntity.accepted()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        application.resynchronizeConfiguration(
                                operationUid,
                                organizationCode,
                                deviceCode,
                                applicationUid,
                                body),
                        TargetRequestIds.resolve(request)));
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> noStore(
            T data, HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }
}
