package org.enveloping.ecobin.identity.web.v1.miniapp;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappActor;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappActorContext;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappLoginService;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappSessionService;
import org.enveloping.ecobin.identity.web.v1.miniapp.FactoryMiniappModels.FactoryMiniappLoginRequest;
import org.enveloping.ecobin.identity.web.v1.miniapp.FactoryMiniappModels.FactoryMiniappSessionCreated;
import org.enveloping.ecobin.identity.web.v1.miniapp.FactoryMiniappModels.FactoryMiniappSessionView;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.net.URI;

@RestController
@RequestMapping("/api/v1/miniapp-factory/auth/sessions")
public class PlatformMiniappAuthController {

    private final PlatformMiniappLoginService loginService;
    private final PlatformMiniappSessionService sessionService;

    public PlatformMiniappAuthController(
            PlatformMiniappLoginService loginService,
            PlatformMiniappSessionService sessionService) {
        this.loginService = loginService;
        this.sessionService = sessionService;
    }

    @PostMapping
    public ResponseEntity<TargetApiEnvelope<
            FactoryMiniappSessionCreated>> login(
            @Valid @RequestBody FactoryMiniappLoginRequest body,
            HttpServletRequest request) {
        return ResponseEntity.created(URI.create(
                        "/api/v1/miniapp-factory/auth/sessions/current"))
                .body(TargetApiEnvelope.ok(
                        loginService.login(body),
                        TargetRequestIds.resolve(request)));
    }

    @GetMapping("/current")
    public TargetApiEnvelope<FactoryMiniappSessionView> current(
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                sessionService.view(actor()),
                TargetRequestIds.resolve(request));
    }

    @DeleteMapping("/current")
    public ResponseEntity<Void> logout() {
        sessionService.revokeCurrent(actor());
        return ResponseEntity.noContent().build();
    }

    private static PlatformMiniappActor actor() {
        return PlatformMiniappActorContext.required();
    }
}
