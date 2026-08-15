package org.enveloping.ecobin.identity.infrastructure.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.framework.web.v1.TargetProblemDetail;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappActor;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappActorContext;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappSessionService;
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

public class PlatformMiniappAuthenticationFilter
        extends OncePerRequestFilter {

    private final PlatformMiniappSessionService sessionService;
    private final ObjectMapper objectMapper;

    public PlatformMiniappAuthenticationFilter(
            PlatformMiniappSessionService sessionService,
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
                    establish(sessionService.resolve(token));
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
            PlatformMiniappActorContext.clear();
            TenantContextHolder.clear();
        }
    }

    private static void establish(PlatformMiniappActor actor) {
        PlatformMiniappActorContext.set(actor);
        TenantContextHolder.setTenantId(null);
        TenantContextHolder.setIgnore(true);
        var authorities = new ArrayList<SimpleGrantedAuthority>();
        authorities.add(new SimpleGrantedAuthority("MINIAPP_FACTORY"));
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
                && request.getRequestURI().endsWith(
                "/api/v1/miniapp-factory/auth/sessions");
    }
}
