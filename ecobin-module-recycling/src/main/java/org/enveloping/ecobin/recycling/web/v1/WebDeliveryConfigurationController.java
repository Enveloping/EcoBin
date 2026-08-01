package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.deliveryconfiguration
        .OrganizationDeliveryConfigurationService;
import org.enveloping.ecobin.recycling.web.v1.DeliveryConfigurationModels
        .DeliveryConfigurationReleaseRequest;
import org.enveloping.ecobin.recycling.web.v1.DeliveryConfigurationModels
        .DeliveryConfigurationVersion;
import org.enveloping.ecobin.recycling.web.v1.DeliveryConfigurationModels
        .DeliveryConfigurationVersionPage;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

@RestController
public class WebDeliveryConfigurationController {

    private final OrganizationDeliveryConfigurationService service;

    public WebDeliveryConfigurationController(
            OrganizationDeliveryConfigurationService service) {
        this.service = service;
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/delivery-configuration")
    public TargetApiEnvelope<DeliveryConfigurationVersion>
    staffCurrent(
            @PathVariable String organizationCode,
            HttpServletRequest request) {
        return ok(
                service.current(
                        false,
                        null,
                        organizationCode),
                request);
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/delivery-configuration")
    public TargetApiEnvelope<DeliveryConfigurationVersion>
    platformCurrent(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            HttpServletRequest request) {
        return ok(
                service.current(
                        true,
                        tenantCode,
                        organizationCode),
                request);
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/delivery-configuration-versions")
    public TargetApiEnvelope<DeliveryConfigurationVersionPage>
    staffVersions(
            @PathVariable String organizationCode,
            @RequestParam(required = false) Long beforeVersionNo,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return ok(
                service.versions(
                        false,
                        null,
                        organizationCode,
                        beforeVersionNo,
                        limit),
                request);
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/delivery-configuration-versions")
    public TargetApiEnvelope<DeliveryConfigurationVersionPage>
    platformVersions(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestParam(required = false) Long beforeVersionNo,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return ok(
                service.versions(
                        true,
                        tenantCode,
                        organizationCode,
                        beforeVersionNo,
                        limit),
                request);
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/delivery-configuration-versions/{versionNo}")
    public TargetApiEnvelope<DeliveryConfigurationVersion>
    staffVersion(
            @PathVariable String organizationCode,
            @PathVariable long versionNo,
            HttpServletRequest request) {
        return ok(
                service.version(
                        false,
                        null,
                        organizationCode,
                        versionNo),
                request);
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/delivery-configuration-versions/{versionNo}")
    public TargetApiEnvelope<DeliveryConfigurationVersion>
    platformVersion(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable long versionNo,
            HttpServletRequest request) {
        return ok(
                service.version(
                        true,
                        tenantCode,
                        organizationCode,
                        versionNo),
                request);
    }

    @PostMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/delivery-configuration-releases")
    public ResponseEntity<
            TargetApiEnvelope<DeliveryConfigurationVersion>>
    staffRelease(
            @PathVariable String organizationCode,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody
            DeliveryConfigurationReleaseRequest body,
            HttpServletRequest request) {
        return created(
                service.release(
                        false,
                        null,
                        organizationCode,
                        operationUid,
                        body),
                request);
    }

    @PostMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/delivery-configuration-releases")
    public ResponseEntity<
            TargetApiEnvelope<DeliveryConfigurationVersion>>
    platformRelease(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody
            DeliveryConfigurationReleaseRequest body,
            HttpServletRequest request) {
        return created(
                service.release(
                        true,
                        tenantCode,
                        organizationCode,
                        operationUid,
                        body),
                request);
    }

    private static <T> TargetApiEnvelope<T> ok(
            T result,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                result,
                TargetRequestIds.resolve(request));
    }

    private static ResponseEntity<
            TargetApiEnvelope<DeliveryConfigurationVersion>>
    created(
            DeliveryConfigurationVersion result,
            HttpServletRequest request) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(TargetApiEnvelope.ok(
                        result,
                        TargetRequestIds.resolve(request)));
    }
}
