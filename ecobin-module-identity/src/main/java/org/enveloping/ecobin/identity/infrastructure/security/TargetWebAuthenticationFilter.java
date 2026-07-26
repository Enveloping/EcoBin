package org.enveloping.ecobin.identity.infrastructure.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedExecutionContext;
import org.enveloping.ecobin.framework.context.TrustedExecutionContextHolder;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.application.web.TargetWebSessionService;
import org.enveloping.ecobin.identity.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.web.v1.TargetProblemDetail;
import org.enveloping.ecobin.identity.web.v1.TargetRequestIds;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public class TargetWebAuthenticationFilter extends OncePerRequestFilter {

    public static final String ACTOR_REQUEST_ATTRIBUTE =
            TargetWebAuthenticationFilter.class.getName() + ".actor";

    private final TargetWebSessionService sessionService;
    private final ObjectMapper objectMapper;

    public TargetWebAuthenticationFilter(
            TargetWebSessionService sessionService,
            ObjectMapper objectMapper) {
        this.sessionService = sessionService;
        this.objectMapper = objectMapper;
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        try {
            String token = cookieToken(request);
            if (StringUtils.hasText(token)) {
                try {
                    TargetWebActor actor = sessionService.resolve(token);
                    if (audienceMismatch(request, actor)) {
                        writeProblem(
                                request,
                                response,
                                401,
                                "AUTH.TOKEN_AUDIENCE_MISMATCH",
                                "登录入口与当前会话不匹配");
                        return;
                    }
                    establish(request, actor);
                } catch (TargetApiException exception) {
                    if (!publicAuthenticationRequest(request)) {
                        writeProblem(
                                request,
                                response,
                                exception.status(),
                                exception.code(),
                                exception.getMessage());
                        return;
                    }
                }
            }
            filterChain.doFilter(request, response);
        } finally {
            TargetWebActorContext.clear();
            TrustedExecutionContextHolder.clear();
            TenantContextHolder.clear();
        }
    }

    private void establish(HttpServletRequest request, TargetWebActor actor) {
        TargetWebActorContext.set(actor);
        request.setAttribute(ACTOR_REQUEST_ATTRIBUTE, actor);
        TenantContextHolder.setIgnore(actor.platform());
        TenantContextHolder.setTenantId(actor.platform() ? null : actor.tenantId());
        TrustedExecutionContextHolder.set(new TrustedExecutionContext(
                actor.platform()
                        ? TrustedPrincipalKind.PLATFORM_ADMIN
                        : TrustedPrincipalKind.STAFF_ACCOUNT,
                actor.principalUid(),
                actor.audience(),
                actor.platform() ? null : stableTenantContextUid(actor.tenantCode()),
                null,
                actor.sessionUid(),
                actor.authVersion(),
                TargetRequestIds.resolve(request)));

        List<SimpleGrantedAuthority> authorities = new ArrayList<>();
        authorities.add(new SimpleGrantedAuthority(
                actor.platform() ? "WEB_PLATFORM" : "WEB_STAFF"));
        actor.effectiveCapabilities().stream()
                .map(code -> new SimpleGrantedAuthority("CAP_" + code))
                .forEach(authorities::add);
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken(
                        actor,
                        null,
                        authorities));
    }

    private void writeProblem(
            HttpServletRequest request,
            HttpServletResponse response,
            int status,
            String code,
            String message) throws IOException {
        response.setStatus(status);
        response.setContentType(MediaType.APPLICATION_PROBLEM_JSON_VALUE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        objectMapper.writeValue(
                response.getOutputStream(),
                new TargetProblemDetail(
                        code,
                        message,
                        TargetRequestIds.resolve(request),
                        false,
                        Map.of()));
    }

    private static String cookieToken(HttpServletRequest request) {
        Cookie[] cookies = request.getCookies();
        if (cookies == null) {
            return null;
        }
        for (Cookie cookie : cookies) {
            if (TargetWebSessionService.COOKIE_NAME.equals(cookie.getName())) {
                return cookie.getValue();
            }
        }
        return null;
    }

    private static boolean publicAuthenticationRequest(
            HttpServletRequest request) {
        String path = request.getRequestURI();
        if (path.endsWith("/api/v1/web/auth/csrf-token")
                && "GET".equals(request.getMethod())) {
            return true;
        }
        return "POST".equals(request.getMethod())
                && (path.endsWith("/api/v1/web/auth/sessions")
                || path.endsWith("/api/v1/web/platform/auth/sessions"));
    }

    private static boolean audienceMismatch(
            HttpServletRequest request,
            TargetWebActor actor) {
        if (publicAuthenticationRequest(request)) {
            return false;
        }
        boolean platformPath = request.getRequestURI()
                .contains("/api/v1/web/platform/");
        return platformPath
                ? actor.audience() != TrustedAudience.WEB_PLATFORM
                : actor.audience() != TrustedAudience.WEB_STAFF;
    }

    private static UUID stableTenantContextUid(String tenantCode) {
        return UUID.nameUUIDFromBytes(
                ("ecobin:tenant:" + tenantCode)
                        .getBytes(StandardCharsets.UTF_8));
    }
}
