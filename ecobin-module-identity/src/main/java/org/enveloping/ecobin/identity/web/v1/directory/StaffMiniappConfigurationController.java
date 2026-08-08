package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.identity.application.directory.TargetMiniappConfigurationService;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.web.v1.directory.MiniappConfigurationModels.MiniappConfigurationView;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/web/organizations/{organizationCode}")
public class StaffMiniappConfigurationController {

    private final TargetMiniappConfigurationService miniapp;

    public StaffMiniappConfigurationController(
            TargetMiniappConfigurationService miniapp) {
        this.miniapp = miniapp;
    }

    @GetMapping("/miniapp-configuration")
    public ResponseEntity<TargetApiEnvelope<MiniappConfigurationView>> get(
            @PathVariable String organizationCode,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(ok(miniapp.get(
                        tenantCode(), organizationCode), request));
    }

    private static String tenantCode() {
        return TargetWebActorContext.required().tenantCode();
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                data, TargetRequestIds.resolve(request));
    }
}
