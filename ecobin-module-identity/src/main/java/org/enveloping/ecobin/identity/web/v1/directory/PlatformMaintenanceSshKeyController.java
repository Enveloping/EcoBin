package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.identity.application.maintenance.PlatformMaintenanceSshKeyService;
import org.enveloping.ecobin.identity.web.v1.directory.MaintenanceSshKeyModels.CreateMaintenanceSshKeyRequest;
import org.enveloping.ecobin.identity.web.v1.directory.MaintenanceSshKeyModels.MaintenanceSshKeyView;
import org.enveloping.ecobin.identity.web.v1.directory.MaintenanceSshKeyModels.RevokeMaintenanceSshKeyRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.net.URI;
import java.util.List;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/platform/maintenance-ssh-keys")
public class PlatformMaintenanceSshKeyController {

    private static final String IDEMPOTENCY_KEY = "Idempotency-Key";

    private final PlatformMaintenanceSshKeyService keys;

    public PlatformMaintenanceSshKeyController(
            PlatformMaintenanceSshKeyService keys) {
        this.keys = keys;
    }

    @GetMapping
    public TargetApiEnvelope<List<MaintenanceSshKeyView>> keys(
            HttpServletRequest request) {
        return ok(keys.listOwnKeys(), request);
    }

    @PostMapping
    public ResponseEntity<TargetApiEnvelope<MaintenanceSshKeyView>> create(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody CreateMaintenanceSshKeyRequest body,
            HttpServletRequest request) {
        MaintenanceSshKeyView created = keys.createOwnKey(
                operationUid, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/maintenance-ssh-keys/"
                                + created.maintenanceSshKeyUid()))
                .body(ok(created, request));
    }

    @PostMapping("/{keyUid}/revocations")
    public TargetApiEnvelope<MaintenanceSshKeyView> revoke(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID keyUid,
            @Valid @RequestBody RevokeMaintenanceSshKeyRequest body,
            HttpServletRequest request) {
        return ok(keys.revokeOwnKey(operationUid, keyUid, body), request);
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(data, TargetRequestIds.resolve(request));
    }
}
