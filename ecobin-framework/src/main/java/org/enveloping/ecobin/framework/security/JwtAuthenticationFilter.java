package org.enveloping.ecobin.framework.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.enums.UserRole;
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

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String token = resolveToken(request);

        if (StringUtils.hasText(token) && jwtTokenProvider.validateToken(token)) {
            // 解析用户信息
            Long userId = jwtTokenProvider.getUserId(token);
            String username = jwtTokenProvider.getUsername(token);
            Long tenantId = jwtTokenProvider.getTenantId(token);
            Integer role = jwtTokenProvider.getRole(token);

            // 强制失效校验：角色/状态变更后旧 token 立即失效
            long iatSec = jwtTokenProvider.getIssuedAt(token).getTime() / 1000;
            if (tokenInvalidationRegistry.isInvalidated(role, userId, tenantId, iatSec)) {
                writeUnauthorized(response, "权限已变更，请重新登录");
                return;
            }

            // 设置租户上下文：平台域（超管/管理员）固定平台池且放行租户过滤；其余按 JWT 租户隔离
            boolean platform = UserRole.isPlatform(role);
            TenantContextHolder.setTenantId(platform ? Constants.PLATFORM_POOL_TENANT_ID : tenantId);
            TenantContextHolder.setIgnore(platform);

            // 由 role 构造 GrantedAuthority（ROLE_SUPER_ADMIN / ROLE_TENANT / ...）
            List<GrantedAuthority> authorities = Collections.emptyList();
            String authority = UserRole.authorityOf(role);
            if (authority != null) {
                authorities = List.of(new SimpleGrantedAuthority("ROLE_" + authority));
            }

            // 设置 Spring Security 认证信息（用于后续的过滤链 或 @PreAuthorize/@PostAuthorize）
            UsernamePasswordAuthenticationToken authentication =
                    new UsernamePasswordAuthenticationToken(username, userId, authorities);
            SecurityContextHolder.getContext().setAuthentication(authentication);
        } else {
            // 无 Token（permitAll 的登录前流程）：放行租户过滤，由业务代码显式指定 tenant_id
            TenantContextHolder.setTenantId(Constants.DEFAULT_TENANT_ID);
            TenantContextHolder.setIgnore(true);
        }

        try {
            filterChain.doFilter(request, response);
        } finally {
            // 清理 ThreadLocal
            TenantContextHolder.clear();
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
}
