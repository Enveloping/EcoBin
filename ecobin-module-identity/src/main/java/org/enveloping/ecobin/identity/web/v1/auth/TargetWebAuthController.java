package org.enveloping.ecobin.identity.web.v1.auth;

import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.application.web.TargetWebSessionService;
import org.enveloping.ecobin.identity.application.web.TargetWebSessionService.AuthenticatedWebSession;
import org.enveloping.ecobin.identity.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.identity.web.v1.TargetRequestIds;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.security.web.csrf.CookieCsrfTokenRepository;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Duration;
import java.util.Arrays;

@RestController
@RequestMapping("/api/v1/web")
public class TargetWebAuthController {

    private final TargetWebSessionService sessionService;
    private final CookieCsrfTokenRepository csrfRepository;

    public TargetWebAuthController(
            TargetWebSessionService sessionService,
            CookieCsrfTokenRepository csrfRepository) {
        this.sessionService = sessionService;
        this.csrfRepository = csrfRepository;
    }

    @GetMapping("/auth/csrf-token")
    public ResponseEntity<TargetApiEnvelope<CsrfTokenView>> csrfToken(
            CsrfToken csrfToken,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        new CsrfTokenView(
                                "X-CSRF-TOKEN",
                                csrfToken.getToken()),
                        TargetRequestIds.resolve(request)));
    }

    @PostMapping("/auth/sessions")
    public ResponseEntity<TargetApiEnvelope<WebSessionView>> loginStaff(
            @Valid @RequestBody WebLoginRequest body,
            HttpServletRequest request,
            HttpServletResponse response) {
        AuthenticatedWebSession session = sessionService.loginStaff(
                body.loginName(),
                body.password(),
                existingToken(request),
                request.getRemoteAddr(),
                request.getHeader(HttpHeaders.USER_AGENT));
        return created(session, request, response);
    }

    @PostMapping("/platform/auth/sessions")
    public ResponseEntity<TargetApiEnvelope<WebSessionView>> loginPlatform(
            @Valid @RequestBody WebLoginRequest body,
            HttpServletRequest request,
            HttpServletResponse response) {
        AuthenticatedWebSession session = sessionService.loginPlatform(
                body.loginName(),
                body.password(),
                existingToken(request),
                request.getRemoteAddr(),
                request.getHeader(HttpHeaders.USER_AGENT));
        return created(session, request, response);
    }

    @GetMapping("/auth/sessions/current")
    public ResponseEntity<TargetApiEnvelope<WebSessionView>> currentStaff(
            HttpServletRequest request) {
        return current(request);
    }

    @GetMapping("/platform/auth/sessions/current")
    public ResponseEntity<TargetApiEnvelope<WebSessionView>> currentPlatform(
            HttpServletRequest request) {
        return current(request);
    }

    @DeleteMapping("/auth/sessions/current")
    public ResponseEntity<Void> logoutStaff(
            HttpServletRequest request,
            HttpServletResponse response) {
        return logout(request, response);
    }

    @DeleteMapping("/platform/auth/sessions/current")
    public ResponseEntity<Void> logoutPlatform(
            HttpServletRequest request,
            HttpServletResponse response) {
        return logout(request, response);
    }

    private ResponseEntity<TargetApiEnvelope<WebSessionView>> created(
            AuthenticatedWebSession session,
            HttpServletRequest request,
            HttpServletResponse response) {
        response.addHeader(
                HttpHeaders.SET_COOKIE,
                ResponseCookie.from(
                                TargetWebSessionService.COOKIE_NAME,
                                session.token())
                        .secure(true)
                        .httpOnly(true)
                        .sameSite("Lax")
                        .path("/")
                        .maxAge(Duration.between(
                                java.time.Instant.now(),
                                session.view().expiresAt()))
                        .build()
                        .toString());
        csrfRepository.saveToken(null, request, response);
        return ResponseEntity.status(201)
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        session.view(),
                        TargetRequestIds.resolve(request)));
    }

    private ResponseEntity<TargetApiEnvelope<WebSessionView>> current(
            HttpServletRequest request) {
        TargetWebActor actor = TargetWebActorContext.required();
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        WebSessionView.from(actor),
                        TargetRequestIds.resolve(request)));
    }

    private ResponseEntity<Void> logout(
            HttpServletRequest request,
            HttpServletResponse response) {
        sessionService.logout(TargetWebActorContext.required());
        response.addHeader(
                HttpHeaders.SET_COOKIE,
                ResponseCookie.from(
                                TargetWebSessionService.COOKIE_NAME,
                                "")
                        .secure(true)
                        .httpOnly(true)
                        .sameSite("Lax")
                        .path("/")
                        .maxAge(Duration.ZERO)
                        .build()
                        .toString());
        csrfRepository.saveToken(null, request, response);
        return ResponseEntity.noContent()
                .cacheControl(CacheControl.noStore())
                .build();
    }

    private static String existingToken(HttpServletRequest request) {
        Cookie[] cookies = request.getCookies();
        if (cookies == null) {
            return null;
        }
        return Arrays.stream(cookies)
                .filter(cookie -> TargetWebSessionService.COOKIE_NAME.equals(
                        cookie.getName()))
                .map(Cookie::getValue)
                .findFirst()
                .orElse(null);
    }
}
