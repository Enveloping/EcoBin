package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.identity.application.directory.TargetMiniappConfigurationService;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.VersionCommand;
import org.enveloping.ecobin.identity.web.v1.directory.MiniappConfigurationModels.MiniappConfigurationMutationView;
import org.enveloping.ecobin.identity.web.v1.directory.MiniappConfigurationModels.MiniappConfigurationView;
import org.enveloping.ecobin.identity.web.v1.directory.MiniappConfigurationModels.PutMiniappConfigurationRequest;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

@RestController
@RequestMapping(
        "/api/v1/web/platform/tenants/{tenantCode}"
                + "/organizations/{organizationCode}")
public class PlatformMiniappConfigurationController {

    private static final String IDEMPOTENCY_KEY = "Idempotency-Key";

    private final TargetMiniappConfigurationService miniapp;

    public PlatformMiniappConfigurationController(
            TargetMiniappConfigurationService miniapp) {
        this.miniapp = miniapp;
    }

    @GetMapping("/miniapp-configuration")
    public ResponseEntity<TargetApiEnvelope<MiniappConfigurationView>> get(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(ok(miniapp.get(
                        tenantCode, organizationCode), request));
    }

    @PutMapping("/miniapp-configuration")
    public TargetApiEnvelope<MiniappConfigurationMutationView> put(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @Valid @RequestBody PutMiniappConfigurationRequest body,
            HttpServletRequest request) {
        return ok(miniapp.put(
                operationUid,
                tenantCode,
                organizationCode,
                body), request);
    }

    @PostMapping("/miniapp-configuration/activations")
    public TargetApiEnvelope<MiniappConfigurationMutationView> activate(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @Valid @RequestBody VersionCommand body,
            HttpServletRequest request) {
        return ok(miniapp.activate(
                operationUid,
                tenantCode,
                organizationCode,
                body), request);
    }

    @PostMapping("/miniapp-login/enablements")
    public TargetApiEnvelope<MiniappConfigurationMutationView> enableLogin(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @Valid @RequestBody VersionCommand body,
            HttpServletRequest request) {
        return ok(miniapp.enableLogin(
                operationUid,
                tenantCode,
                organizationCode,
                body), request);
    }

    @PostMapping("/miniapp-login/disablements")
    public TargetApiEnvelope<MiniappConfigurationMutationView> disableLogin(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @Valid @RequestBody VersionCommand body,
            HttpServletRequest request) {
        return ok(miniapp.disableLogin(
                operationUid,
                tenantCode,
                organizationCode,
                body), request);
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                data, TargetRequestIds.resolve(request));
    }
}
