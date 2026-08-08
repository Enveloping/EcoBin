package org.enveloping.ecobin.identity.web.v1.miniapp;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActor;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActorContext;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappLoginService;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappOrganizationAccountService;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappPhoneBindingService;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappSessionService;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.MiniappLoginRequest;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.MiniappSessionCreated;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.MiniappSessionView;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.BindPhoneRequest;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.PhoneBindingResult;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.PhoneBindingView;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.OrganizationAccountList;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.OrganizationAccountSelectionRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

import java.net.URI;

@RestController
public class TargetMiniappAuthController {

    private final TargetMiniappLoginService loginService;
    private final TargetMiniappSessionService sessionService;
    private final TargetMiniappPhoneBindingService phoneBindingService;
    private final TargetMiniappOrganizationAccountService organizationAccountService;

    public TargetMiniappAuthController(
            TargetMiniappLoginService loginService,
            TargetMiniappSessionService sessionService,
            TargetMiniappPhoneBindingService phoneBindingService,
            TargetMiniappOrganizationAccountService organizationAccountService) {
        this.loginService = loginService;
        this.sessionService = sessionService;
        this.phoneBindingService = phoneBindingService;
        this.organizationAccountService = organizationAccountService;
    }

    @PostMapping("/api/v1/miniapp/auth/sessions")
    public ResponseEntity<TargetApiEnvelope<MiniappSessionCreated>> login(
            @Valid @RequestBody MiniappLoginRequest body,
            HttpServletRequest request) {
        MiniappSessionCreated created = loginService.login(body);
        String prefix = "miniapp-staff".equals(created.audience())
                ? "/api/v1/miniapp-staff"
                : "/api/v1/miniapp";
        return ResponseEntity.created(
                        URI.create(prefix + "/auth/sessions/current"))
                .body(TargetApiEnvelope.ok(
                        created, TargetRequestIds.resolve(request)));
    }

    @GetMapping({
            "/api/v1/miniapp/auth/sessions/current",
            "/api/v1/miniapp-staff/auth/sessions/current"
    })
    public TargetApiEnvelope<MiniappSessionView> current(
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                sessionService.view(actor()),
                TargetRequestIds.resolve(request));
    }

    @DeleteMapping({
            "/api/v1/miniapp/auth/sessions/current",
            "/api/v1/miniapp-staff/auth/sessions/current"
    })
    public ResponseEntity<Void> logout() {
        sessionService.revokeCurrent(actor());
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/api/v1/miniapp/me/phone-bindings")
    public ResponseEntity<TargetApiEnvelope<PhoneBindingView>> bindPhone(
            @RequestHeader("Idempotency-Key") java.util.UUID operationUid,
            @Valid @RequestBody BindPhoneRequest body,
            HttpServletRequest request) {
        PhoneBindingResult result = phoneBindingService.bind(
                operationUid, actor(), body.wechatPhoneCode());
        ResponseEntity.BodyBuilder response = result.created()
                ? ResponseEntity.created(
                URI.create("/api/v1/miniapp/me/phone-bindings/current"))
                : ResponseEntity.ok();
        return response.body(TargetApiEnvelope.ok(
                result.binding(), TargetRequestIds.resolve(request)));
    }

    @GetMapping({
            "/api/v1/miniapp/me/organization-accounts",
            "/api/v1/miniapp-staff/me/organization-accounts"
    })
    public TargetApiEnvelope<OrganizationAccountList> organizationAccounts(
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                organizationAccountService.list(actor()),
                TargetRequestIds.resolve(request));
    }

    @PostMapping({
            "/api/v1/miniapp/auth/organization-account-selections",
            "/api/v1/miniapp-staff/auth/organization-account-selections"
    })
    public TargetApiEnvelope<MiniappSessionCreated> selectOrganizationAccount(
            @RequestHeader("Idempotency-Key") java.util.UUID operationUid,
            @Valid @RequestBody OrganizationAccountSelectionRequest body,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                organizationAccountService.select(
                        operationUid,
                        body.organizationUserUid(),
                        actor()),
                TargetRequestIds.resolve(request));
    }

    private static TargetMiniappActor actor() {
        return TargetMiniappActorContext.required();
    }
}
