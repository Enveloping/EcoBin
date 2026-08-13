package org.enveloping.ecobin.device.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.installation.DeviceInstallationProfileService;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeviceInstallationProfileView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.UpdateDeviceInstallationProfileRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class MiniappDeviceInstallationController {

    private final DeviceInstallationProfileService service;

    public MiniappDeviceInstallationController(
            DeviceInstallationProfileService service) {
        this.service = service;
    }

    @GetMapping(
            "/api/v1/miniapp/devices/{deviceCode}/installation-profile")
    public TargetApiEnvelope<DeviceInstallationProfileView> get(
            @PathVariable String deviceCode,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                service.get(deviceCode),
                TargetRequestIds.resolve(request));
    }

    @PutMapping(
            "/api/v1/miniapp/devices/{deviceCode}/installation-profile")
    public TargetApiEnvelope<DeviceInstallationProfileView> update(
            @PathVariable String deviceCode,
            @Valid @RequestBody UpdateDeviceInstallationProfileRequest body,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                service.update(deviceCode, body),
                TargetRequestIds.resolve(request));
    }
}
