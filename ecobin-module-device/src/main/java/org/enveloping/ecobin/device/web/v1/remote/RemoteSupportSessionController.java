package org.enveloping.ecobin.device.web.v1.remote;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.remote.RemoteSupportSessionService;
import org.enveloping.ecobin.device.web.v1.remote.RemoteSupportModels.CloseRemoteSupportRequest;
import org.enveloping.ecobin.device.web.v1.remote.RemoteSupportModels.OpenRemoteSupportRequest;
import org.enveloping.ecobin.device.web.v1.remote.RemoteSupportModels.RemoteSupportSessionView;
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
import org.springframework.web.bind.annotation.RestController;

import java.net.URI;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/platform")
public class RemoteSupportSessionController {

    private static final String IDEMPOTENCY_KEY = "Idempotency-Key";

    private final RemoteSupportSessionService sessions;

    public RemoteSupportSessionController(
            RemoteSupportSessionService sessions) {
        this.sessions = sessions;
    }

    @PostMapping("/device-assets/{hardwareSn}/remote-support-sessions")
    public ResponseEntity<TargetApiEnvelope<RemoteSupportSessionView>> open(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String hardwareSn,
            @Valid @RequestBody OpenRemoteSupportRequest body,
            HttpServletRequest request) {
        RemoteSupportSessionView created = sessions.open(
                operationUid, hardwareSn, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/remote-support-sessions/"
                                + created.sessionUid()))
                .cacheControl(CacheControl.noStore())
                .body(ok(created, request));
    }

    @GetMapping("/remote-support-sessions/{sessionUid}")
    public ResponseEntity<TargetApiEnvelope<RemoteSupportSessionView>> detail(
            @PathVariable UUID sessionUid,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(ok(sessions.detail(sessionUid), request));
    }

    @GetMapping("/device-assets/{hardwareSn}/remote-support-sessions/current")
    public ResponseEntity<TargetApiEnvelope<RemoteSupportSessionView>> current(
            @PathVariable String hardwareSn,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(ok(sessions.currentForAsset(hardwareSn), request));
    }

    @PostMapping("/remote-support-sessions/{sessionUid}/closures")
    public ResponseEntity<TargetApiEnvelope<RemoteSupportSessionView>> close(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID sessionUid,
            @Valid @RequestBody CloseRemoteSupportRequest body,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(ok(sessions.close(
                        operationUid, sessionUid, body), request));
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(data, TargetRequestIds.resolve(request));
    }
}
