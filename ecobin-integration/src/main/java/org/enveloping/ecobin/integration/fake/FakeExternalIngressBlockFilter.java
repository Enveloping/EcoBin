package org.enveloping.ecobin.integration.fake;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * Fake 环境的真实设备/渠道入站闩锁。
 */
public final class FakeExternalIngressBlockFilter
        extends OncePerRequestFilter {

    private static final List<String> BLOCKED_PREFIXES = List.of(
            "/api/iot",
            "/api/integration/onenet",
            "/api/integration/wechat",
            "/api/wechat/notify",
            "/api/funds/wechat/notify",
            "/api/v1/wechat-pay/notifications");

    private final boolean enabled;

    public FakeExternalIngressBlockFilter(boolean enabled) {
        this.enabled = enabled;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        if (!enabled) {
            return true;
        }
        String requestUri = request.getRequestURI();
        String contextPath = request.getContextPath();
        String path = requestUri.substring(contextPath.length());
        return BLOCKED_PREFIXES.stream().noneMatch(path::startsWith);
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        response.setStatus(HttpServletResponse.SC_SERVICE_UNAVAILABLE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setContentType(MediaType.APPLICATION_PROBLEM_JSON_VALUE);
        response.getWriter().write(
                "{\"type\":\"about:blank\","
                        + "\"title\":\"External ingress disabled\","
                        + "\"status\":503,"
                        + "\"detail\":\"Fake environment blocks real device and channel ingress\"}");
    }
}
