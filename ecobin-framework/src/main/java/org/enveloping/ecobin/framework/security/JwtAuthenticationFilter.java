package org.enveloping.ecobin.framework.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.framework.context.TrustedExecutionContextHolder;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.Collections;
import java.util.List;
import java.util.UUID;

/**
 * JWT 认证过滤器：从请求头中提取 Token 并解析用户信息
 *  构建租户上下文
 *  标识当前请求用户的权限
 *  校验 token 是否被强制失效（角色/状态变更）
 */
@Component
@RequiredArgsConstructor
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private final JwtTokenProvider jwtTokenProvider;
    private final TokenInvalidationRegistry tokenInvalidationRegistry;
    private final TrustedSessionResolver trustedSessionResolver;

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        try {
            String token = resolveToken(request);

            if (StringUtils.hasText(token) && jwtTokenProvider.validateToken(token)) {
                try {
                    JwtSessionClaims claims = jwtTokenProvider.parseSession(token);
                    ResolvedTrustedSession session = trustedSessionResolver.resolve(claims);

                    // 强制失效校验使用 identity 按当前数据库事实解析出的主体和作用域。
                    long iatSec = claims.issuedAt().getEpochSecond();
                    if (tokenInvalidationRegistry.isInvalidated(
                            session.legacyRole(),
                            session.legacyPrincipalId(),
                            session.legacyTenantId(),
                            iatSec)) {
                        writeUnauthorized(response, "权限已变更，请重新登录");
                        return;
                    }

                    boolean platform = session.context().principalKind() == TrustedPrincipalKind.PLATFORM_ADMIN;
                    TenantContextHolder.setTenantId(
                            platform ? Constants.PLATFORM_POOL_TENANT_ID : session.legacyTenantId());
                    TenantContextHolder.setIgnore(platform);
                    TrustedExecutionContextHolder.set(
                            session.context().withRequestId(resolveRequestId(request)));

                    List<GrantedAuthority> authorities = Collections.emptyList();
                    String authority = org.enveloping.ecobin.common.enums.UserRole.authorityOf(session.legacyRole());
                    if (authority != null) {
                        authorities = List.of(new SimpleGrantedAuthority("ROLE_" + authority));
                    }

                    UsernamePasswordAuthenticationToken authentication =
                            new UsernamePasswordAuthenticationToken(
                                    session.authenticationName(),
                                    session.legacyPrincipalId(),
                                    authorities);
                    SecurityContextHolder.getContext().setAuthentication(authentication);
                } catch (TrustedSessionRejectedException | IllegalArgumentException e) {
                    writeUnauthorized(response, "登录状态无效，请重新登录");
                    return;
                }
            } else {
                // 无 Token（permitAll 的登录前流程）：放行租户过滤，由业务代码显式指定 tenant_id
                TenantContextHolder.setTenantId(Constants.DEFAULT_TENANT_ID);
                TenantContextHolder.setIgnore(true);
            }

            filterChain.doFilter(request, response);
        } finally {
            // 覆盖 resolver 拒绝、提前返回和下游异常，防止线程复用时泄漏请求上下文。
            TenantContextHolder.clear();
            TrustedExecutionContextHolder.clear();
        }
    }

    /**
     * 输出 401 响应体（统一 Result 结构）
     */
    private void writeUnauthorized(HttpServletResponse response, String message) throws IOException {
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding("UTF-8");
        response.getWriter().write("{\"code\":401,\"message\":\"" + message + "\",\"data\":null}");
    }

    /**
     * 从请求头中解析 JWT Token
     */
    private String resolveToken(HttpServletRequest request) {
        String bearerToken = request.getHeader(Constants.TOKEN_HEADER);
        if (StringUtils.hasText(bearerToken) && bearerToken.startsWith(Constants.TOKEN_PREFIX)) {
            return bearerToken.substring(Constants.TOKEN_PREFIX.length());
        }
        return null;
    }

    private String resolveRequestId(HttpServletRequest request) {
        String requestId = request.getHeader("X-Request-ID");
        return StringUtils.hasText(requestId) ? requestId : UUID.randomUUID().toString();
    }
}
