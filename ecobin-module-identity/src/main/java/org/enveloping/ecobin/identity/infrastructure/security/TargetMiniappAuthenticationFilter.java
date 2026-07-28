package org.enveloping.ecobin.identity.infrastructure.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedExecutionContext;
import org.enveloping.ecobin.framework.context.TrustedExecutionContextHolder;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActor;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActorContext;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappSessionService;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.framework.web.v1.TargetProblemDetail;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
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
import java.util.Map;
import java.util.UUID;

public class TargetMiniappAuthenticationFilter
        extends OncePerRequestFilter {

    private final TargetMiniappSessionService sessionService;
    private final ObjectMapper objectMapper;

    public TargetMiniappAuthenticationFilter(
            TargetMiniappSessionService sessionService,
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
            String token = bearerToken(request);
            if (StringUtils.hasText(token)) {
                try {
                    TargetMiniappActor actor = sessionService.resolve(token);
                    if (audienceMismatch(request, actor)) {
                        writeProblem(
                                request,
                                response,
                                401,
                                "AUTH.TOKEN_AUDIENCE_MISMATCH",
                                "小程序入口与当前会话不匹配",
                                false,
                                Map.of());
                        return;
                    }
                    establish(request, actor);
                } catch (TargetApiException exception) {
                    if (!publicLogin(request)) {
                        writeProblem(
                                request,
                                response,
                                exception.status(),
                                exception.code(),
                                exception.getMessage(),
                                exception.retryable(),
                                exception.details());
                        return;
                    }
                }
            }
            filterChain.doFilter(request, response);
        } finally {
            TargetMiniappActorContext.clear();
            TrustedExecutionContextHolder.clear();
            TenantContextHolder.clear();
        }
    }

    private void establish(
            HttpServletRequest request,
            TargetMiniappActor actor) {
        TargetMiniappActorContext.set(actor);
        TenantContextHolder.setIgnore(false);
        TenantContextHolder.setTenantId(actor.tenantId());
        TrustedExecutionContextHolder.set(new TrustedExecutionContext(
                actor.principalKind(),
                actor.principalUid(),
                actor.audience(),
                stableUid("tenant", actor.tenantCode()),
                stableUid(
                        "organization",
                        actor.tenantCode() + ":" + actor.organizationCode()),
                actor.sessionUid(),
                actor.authVersion(),
                TargetRequestIds.resolve(request)));
        var authorities = new ArrayList<SimpleGrantedAuthority>();
        authorities.add(new SimpleGrantedAuthority(
                actor.audience() == TrustedAudience.MINIAPP
                        ? "MINIAPP"
                        : "MINIAPP_STAFF"));
        actor.capabilities().stream()
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
            String message,
            boolean retryable,
            Map<String, Object> details) throws IOException {
        response.setStatus(status);
        response.setContentType(MediaType.APPLICATION_PROBLEM_JSON_VALUE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        objectMapper.writeValue(
                response.getOutputStream(),
                new TargetProblemDetail(
                        code,
                        message,
                        TargetRequestIds.resolve(request),
                        retryable,
                        details));
    }

    private static String bearerToken(HttpServletRequest request) {
        String authorization = request.getHeader("Authorization");
        if (!StringUtils.hasText(authorization)
                || !authorization.startsWith("Bearer ")) {
            return null;
        }
        return authorization.substring("Bearer ".length());
    }

    private static boolean publicLogin(HttpServletRequest request) {
        return "POST".equals(request.getMethod())
                && request.getRequestURI()
                .endsWith("/api/v1/miniapp/auth/sessions");
    }

    private static boolean audienceMismatch(
            HttpServletRequest request,
            TargetMiniappActor actor) {
        if (publicLogin(request)) {
            return false;
        }
        boolean staffPath = request.getRequestURI()
                .contains("/api/v1/miniapp-staff/");
        return staffPath
                ? actor.audience() != TrustedAudience.MINIAPP_STAFF
                : actor.audience() != TrustedAudience.MINIAPP;
    }

    private static UUID stableUid(String kind, String value) {
        return UUID.nameUUIDFromBytes(
                ("ecobin:" + kind + ":" + value)
                        .getBytes(StandardCharsets.UTF_8));
    }
}
