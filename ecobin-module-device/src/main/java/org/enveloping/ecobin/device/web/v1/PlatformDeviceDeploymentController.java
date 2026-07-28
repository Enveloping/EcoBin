package org.enveloping.ecobin.device.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.target.TargetDeviceApplication;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ActivateDeploymentRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationAcceptedView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationApplicationView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationReleaseRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationVersionSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationVersionView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.CreateDeploymentRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.CursorPage;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeploymentRuntimeView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeploymentVersionCommand;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeploymentView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.PageData;
import org.enveloping.ecobin.device.web.v1.DeviceModels.PortRuntimeView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.PortView;
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
@RequestMapping("/api/v1/web/platform/tenants/{tenantCode}"
        + "/organizations/{organizationCode}/device-deployments")
public class PlatformDeviceDeploymentController {

    private final TargetDeviceApplication application;

    public PlatformDeviceDeploymentController(
            TargetDeviceApplication application) {
        this.application = application;
    }

    @PostMapping
    public ResponseEntity<TargetApiEnvelope<DeploymentView>> create(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @Valid @RequestBody CreateDeploymentRequest body,
            HttpServletRequest request) {
        DeploymentView created = application.createDeployment(
                operationUid, tenantCode, organizationCode, body);
        return ResponseEntity.created(URI.create(
                        deploymentBase(
                                tenantCode, organizationCode)
                                + "/" + created.deploymentCode()))
                .cacheControl(CacheControl.noStore())
                .body(ok(created, request));
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<PageData<DeploymentView>>> list(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String lifecycleStatus,
            @RequestParam(required = false) Boolean businessEnabled,
            @RequestParam(required = false) String hardwareSn,
            @RequestParam(required = false) String edgeConnectionStatus,
            @RequestParam(required = false)
            String configurationApplicationStatus,
            HttpServletRequest request) {
        return noStore(application.listDeployments(
                true,
                tenantCode,
                organizationCode,
                page,
                pageSize,
                lifecycleStatus,
                businessEnabled,
                hardwareSn,
                edgeConnectionStatus,
                configurationApplicationStatus), request);
    }

    @GetMapping("/{deploymentCode}")
    public ResponseEntity<TargetApiEnvelope<DeploymentView>> detail(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deploymentCode,
            HttpServletRequest request) {
        return noStore(application.deployment(
                true,
                tenantCode,
                organizationCode,
                deploymentCode), request);
    }

    @GetMapping("/{deploymentCode}/ports")
    public ResponseEntity<TargetApiEnvelope<List<PortView>>> ports(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deploymentCode,
            HttpServletRequest request) {
        return noStore(application.ports(
                true,
                tenantCode,
                organizationCode,
                deploymentCode), request);
    }

    @GetMapping("/{deploymentCode}/runtime")
    public ResponseEntity<TargetApiEnvelope<DeploymentRuntimeView>> runtime(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deploymentCode,
            HttpServletRequest request) {
        return noStore(application.runtime(
                true,
                tenantCode,
                organizationCode,
                deploymentCode), request);
    }

    @GetMapping("/{deploymentCode}/ports/{portNo}/runtime")
    public ResponseEntity<TargetApiEnvelope<PortRuntimeView>> portRuntime(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deploymentCode,
            @PathVariable int portNo,
            HttpServletRequest request) {
        return noStore(application.portRuntime(
                true,
                tenantCode,
                organizationCode,
                deploymentCode,
                portNo), request);
    }

    @PostMapping("/{deploymentCode}/activations")
    public TargetApiEnvelope<DeploymentView> activate(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deploymentCode,
            @Valid @RequestBody ActivateDeploymentRequest body,
            HttpServletRequest request) {
        return ok(application.activate(
                operationUid,
                true,
                tenantCode,
                organizationCode,
                deploymentCode,
                body), request);
    }

    @PostMapping("/{deploymentCode}/deactivations")
    public TargetApiEnvelope<DeploymentView> deactivate(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deploymentCode,
            @Valid @RequestBody DeploymentVersionCommand body,
            HttpServletRequest request) {
        return ok(application.deactivate(
                operationUid,
                true,
                tenantCode,
                organizationCode,
                deploymentCode,
                body), request);
    }

    @PostMapping("/{deploymentCode}/business-switch/enablements")
    public TargetApiEnvelope<DeploymentView> enableBusiness(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deploymentCode,
            @Valid @RequestBody DeploymentVersionCommand body,
            HttpServletRequest request) {
        return ok(application.enableBusiness(
                operationUid,
                true,
                tenantCode,
                organizationCode,
                deploymentCode,
                body), request);
    }

    @PostMapping("/{deploymentCode}/business-switch/disablements")
    public TargetApiEnvelope<DeploymentView> disableBusiness(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deploymentCode,
            @Valid @RequestBody DeploymentVersionCommand body,
            HttpServletRequest request) {
        return ok(application.disableBusiness(
                operationUid,
                true,
                tenantCode,
                organizationCode,
                deploymentCode,
                body), request);
    }

    @GetMapping("/{deploymentCode}/configuration-versions")
    public ResponseEntity<
            TargetApiEnvelope<CursorPage<ConfigurationVersionSummary>>>
            configurationVersions(
                    @PathVariable String tenantCode,
                    @PathVariable String organizationCode,
                    @PathVariable String deploymentCode,
                    @RequestParam(required = false) Long beforeVersionNo,
                    @RequestParam(defaultValue = "20") int limit,
                    HttpServletRequest request) {
        return noStore(application.configurationVersions(
                true,
                tenantCode,
                organizationCode,
                deploymentCode,
                beforeVersionNo,
                limit), request);
    }

    @GetMapping("/{deploymentCode}/configuration-versions/{versionNo}")
    public ResponseEntity<TargetApiEnvelope<ConfigurationVersionView>>
            configurationVersion(
                    @PathVariable String tenantCode,
                    @PathVariable String organizationCode,
                    @PathVariable String deploymentCode,
                    @PathVariable long versionNo,
                    HttpServletRequest request) {
        return noStore(application.configurationVersion(
                true,
                tenantCode,
                organizationCode,
                deploymentCode,
                versionNo), request);
    }

    @PostMapping("/{deploymentCode}/configuration-releases")
    public ResponseEntity<TargetApiEnvelope<ConfigurationAcceptedView>>
            releaseConfiguration(
                    @RequestHeader("Idempotency-Key") UUID operationUid,
                    @PathVariable String tenantCode,
                    @PathVariable String organizationCode,
                    @PathVariable String deploymentCode,
                    @Valid @RequestBody ConfigurationReleaseRequest body,
                    HttpServletRequest request) {
        ConfigurationAcceptedView accepted =
                application.releaseConfiguration(
                        operationUid,
                        true,
                        tenantCode,
                        organizationCode,
                        deploymentCode,
                        body);
        return accepted(accepted, request);
    }

    @GetMapping("/{deploymentCode}/configuration-applications"
            + "/{applicationUid}")
    public ResponseEntity<TargetApiEnvelope<ConfigurationApplicationView>>
            configurationApplication(
                    @PathVariable String tenantCode,
                    @PathVariable String organizationCode,
                    @PathVariable String deploymentCode,
                    @PathVariable UUID applicationUid,
                    HttpServletRequest request) {
        return noStore(application.configurationApplication(
                true,
                tenantCode,
                organizationCode,
                deploymentCode,
                applicationUid), request);
    }

    @PostMapping("/{deploymentCode}/configuration-applications"
            + "/{applicationUid}/resynchronizations")
    public ResponseEntity<TargetApiEnvelope<ConfigurationAcceptedView>>
            resynchronize(
                    @RequestHeader("Idempotency-Key") UUID operationUid,
                    @PathVariable String tenantCode,
                    @PathVariable String organizationCode,
                    @PathVariable String deploymentCode,
                    @PathVariable UUID applicationUid,
                    @Valid @RequestBody DeploymentVersionCommand body,
                    HttpServletRequest request) {
        ConfigurationAcceptedView accepted =
                application.resynchronizeConfiguration(
                        operationUid,
                        true,
                        tenantCode,
                        organizationCode,
                        deploymentCode,
                        applicationUid,
                        body);
        return accepted(accepted, request);
    }

    private static ResponseEntity<TargetApiEnvelope<ConfigurationAcceptedView>>
            accepted(
                    ConfigurationAcceptedView data,
                    HttpServletRequest request) {
        return ResponseEntity.accepted()
                .location(URI.create(data.statusUrl()))
                .cacheControl(CacheControl.noStore())
                .body(ok(data, request));
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

    private static String deploymentBase(
            String tenantCode,
            String organizationCode) {
        return "/api/v1/web/platform/tenants/" + tenantCode
                + "/organizations/" + organizationCode
                + "/device-deployments";
    }
}
