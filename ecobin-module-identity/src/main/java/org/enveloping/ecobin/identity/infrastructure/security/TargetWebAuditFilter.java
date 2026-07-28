package org.enveloping.ecobin.identity.infrastructure.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext.Descriptor;
import org.enveloping.ecobin.identity.application.web.TargetWebRequestAuditService;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

public class TargetWebAuditFilter extends OncePerRequestFilter {

    private final TargetWebRequestAuditService auditService;

    public TargetWebAuditFilter(TargetWebRequestAuditService auditService) {
        this.auditService = auditService;
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        int exceptionalStatus = 0;
        try {
            filterChain.doFilter(request, response);
        } catch (ServletException | IOException | RuntimeException exception) {
            exceptionalStatus = 500;
            throw exception;
        } finally {
            int status = exceptionalStatus == 0
                    ? response.getStatus() : exceptionalStatus;
            TargetWebActor actor = (TargetWebActor) request.getAttribute(
                    TargetWebAuthenticationFilter.ACTOR_REQUEST_ATTRIBUTE);
            Descriptor descriptor = (Descriptor) request.getAttribute(
                    TargetWebAuditRequestContext.ATTRIBUTE);
            auditService.record(
                    request.getMethod(),
                    request.getRequestURI(),
                    status,
                    request.getHeader("Idempotency-Key"),
                    actor,
                    descriptor);
        }
    }
}
