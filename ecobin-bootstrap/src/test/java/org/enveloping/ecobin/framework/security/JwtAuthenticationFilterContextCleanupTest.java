package org.enveloping.ecobin.framework.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.enums.UserRole;
import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedExecutionContext;
import org.enveloping.ecobin.framework.context.TrustedExecutionContextHolder;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.security.core.context.SecurityContextHolder;

import java.time.Instant;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class JwtAuthenticationFilterContextCleanupTest {

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
        TrustedExecutionContextHolder.clear();
        SecurityContextHolder.clearContext();
    }

    @Test
    void clearsRequestThreadLocalsWhenDownstreamThrowsAfterContextWasSet() throws Exception {
        JwtTokenProvider tokenProvider = mock(JwtTokenProvider.class);
        TokenInvalidationRegistry invalidationRegistry = mock(TokenInvalidationRegistry.class);
        TrustedSessionResolver sessionResolver = mock(TrustedSessionResolver.class);
        HttpServletRequest request = mock(HttpServletRequest.class);
        HttpServletResponse response = mock(HttpServletResponse.class);
        FilterChain filterChain = mock(FilterChain.class);
        JwtAuthenticationFilter filter =
                new JwtAuthenticationFilter(tokenProvider, invalidationRegistry, sessionResolver);

        UUID jti = UUID.randomUUID();
        JwtSessionClaims claims = new JwtSessionClaims(
                7L,
                2L,
                UserRole.USER.getCode(),
                "openid",
                jti,
                TrustedAudience.MINIAPP,
                Instant.now());
        TrustedExecutionContext context = new TrustedExecutionContext(
                TrustedPrincipalKind.ORGANIZATION_USER,
                UUID.randomUUID(),
                TrustedAudience.MINIAPP,
                UUID.randomUUID(),
                UUID.randomUUID(),
                jti,
                0,
                "unassigned");
        ResolvedTrustedSession session = new ResolvedTrustedSession(
                context,
                7L,
                2L,
                UserRole.USER.getCode(),
                "openid");

        when(request.getHeader(Constants.TOKEN_HEADER)).thenReturn("Bearer signed-token");
        when(tokenProvider.validateToken("signed-token")).thenReturn(true);
        when(tokenProvider.parseSession("signed-token")).thenReturn(claims);
        when(sessionResolver.resolve(claims)).thenReturn(session);
        when(invalidationRegistry.isInvalidated(
                UserRole.USER.getCode(), 7L, 2L, claims.issuedAt().getEpochSecond()))
                .thenReturn(false);
        doThrow(new ServletException("downstream failed")).when(filterChain).doFilter(request, response);

        assertThrows(
                ServletException.class,
                () -> filter.doFilterInternal(request, response, filterChain));

        assertNull(TenantContextHolder.getTenantId());
        assertFalse(TenantContextHolder.isIgnore());
        assertThrows(IllegalStateException.class, TrustedExecutionContextHolder::getRequired);
    }
}
