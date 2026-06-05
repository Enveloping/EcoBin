package org.enveloping.ecobin.framework.security;

import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;

/**
 * 安全上下文工具：从 Spring Security 上下文中读取当前登录用户信息。
 * <p>
 * {@link JwtAuthenticationFilter} 将 userId 放在 {@code Authentication.getCredentials()}，
 * username 放在 {@code getPrincipal()}。此处集中读取，避免各处散落地强转 credentials。
 */
public final class SecurityUtils {

    private SecurityUtils() {
    }

    /**
     * 获取当前登录用户的 userId（终端用户/租户/管理员的主体 id）。
     *
     * @return userId；未认证或无凭证时返回 null
     */
    public static Long getCurrentUserId() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null) {
            return null;
        }
        Object credentials = authentication.getCredentials();
        return credentials instanceof Long userId ? userId : null;
    }

    /**
     * 获取当前登录用户名（管理员/租户为用户名，小程序用户为 openid）。
     *
     * @return username；未认证时返回 null
     */
    public static String getCurrentUsername() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null) {
            return null;
        }
        Object principal = authentication.getPrincipal();
        return principal instanceof String username ? username : null;
    }
}
