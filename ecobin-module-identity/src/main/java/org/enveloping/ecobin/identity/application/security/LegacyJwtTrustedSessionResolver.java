package org.enveloping.ecobin.identity.application.security;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.enums.UserRole;
import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedExecutionContext;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;
import org.enveloping.ecobin.framework.security.JwtSessionClaims;
import org.enveloping.ecobin.framework.security.ResolvedTrustedSession;
import org.enveloping.ecobin.framework.security.TrustedSessionRejectedException;
import org.enveloping.ecobin.framework.security.TrustedSessionResolver;
import org.enveloping.ecobin.identity.application.support.LegacyIdentityUidFactory;
import org.enveloping.ecobin.identity.infrastructure.persistence.entity.Admin;
import org.enveloping.ecobin.identity.infrastructure.persistence.entity.Tenant;
import org.enveloping.ecobin.identity.infrastructure.persistence.entity.User;
import org.enveloping.ecobin.identity.infrastructure.persistence.mapper.AdminMapper;
import org.enveloping.ecobin.identity.infrastructure.persistence.mapper.TenantMapper;
import org.enveloping.ecobin.identity.infrastructure.persistence.mapper.UserMapper;
import org.springframework.stereotype.Component;

/**
 * F-02 迁移期把已验签 JWT 与当前旧库主体状态合并为可信上下文。
 */
@Component
@RequiredArgsConstructor
public class LegacyJwtTrustedSessionResolver implements TrustedSessionResolver {

    private final AdminMapper adminMapper;
    private final TenantMapper tenantMapper;
    private final UserMapper userMapper;

    @Override
    public ResolvedTrustedSession resolve(JwtSessionClaims claims) {
        int role = claims.legacyRole();
        if (role == UserRole.SUPER_ADMIN.getCode() || role == UserRole.ADMIN.getCode()) {
            return resolvePlatform(claims);
        }
        if (role == UserRole.TENANT.getCode()) {
            return resolveTenant(claims);
        }
        if (role == UserRole.DEVICE_ADMIN.getCode()
                || role == UserRole.CLEANER.getCode()
                || role == UserRole.USER.getCode()) {
            return resolveOrganizationUser(claims);
        }
        throw new TrustedSessionRejectedException("unknown legacy role");
    }

    private ResolvedTrustedSession resolvePlatform(JwtSessionClaims claims) {
        Admin admin = adminMapper.selectById(claims.legacyPrincipalId());
        require(admin != null && enabled(admin.getStatus()), "platform account is unavailable");
        require(admin.getRole().equals(claims.legacyRole()), "platform authority changed");
        require(claims.audience() == TrustedAudience.WEB_PLATFORM, "platform audience mismatch");
        TrustedExecutionContext context = new TrustedExecutionContext(
                TrustedPrincipalKind.PLATFORM_ADMIN,
                LegacyIdentityUidFactory.principal("platform", admin.getId()).value(),
                TrustedAudience.WEB_PLATFORM,
                null,
                null,
                claims.jti(),
                0,
                "unassigned");
        return new ResolvedTrustedSession(
                context, admin.getId(), claims.legacyTenantId(), admin.getRole(), admin.getUsername());
    }

    private ResolvedTrustedSession resolveTenant(JwtSessionClaims claims) {
        Tenant tenant = tenantMapper.selectById(claims.legacyPrincipalId());
        require(tenant != null && enabled(tenant.getStatus()), "tenant account is unavailable");
        require(tenant.getId().equals(claims.legacyTenantId()), "tenant scope mismatch");
        require(claims.audience() == TrustedAudience.WEB_STAFF, "staff audience mismatch");
        TrustedExecutionContext context = new TrustedExecutionContext(
                TrustedPrincipalKind.STAFF_ACCOUNT,
                LegacyIdentityUidFactory.principal("tenant", tenant.getId()).value(),
                TrustedAudience.WEB_STAFF,
                LegacyIdentityUidFactory.tenant(tenant.getId()).value(),
                null,
                claims.jti(),
                0,
                "unassigned");
        return new ResolvedTrustedSession(
                context, tenant.getId(), tenant.getId(), UserRole.TENANT.getCode(), tenant.getUsername());
    }

    private ResolvedTrustedSession resolveOrganizationUser(JwtSessionClaims claims) {
        User user = userMapper.selectById(claims.legacyPrincipalId());
        require(user != null && enabled(user.getStatus()), "organization user is unavailable");
        require(user.getTenantId().equals(claims.legacyTenantId()), "organization scope mismatch");
        require(user.getRole().equals(claims.legacyRole()), "organization user authority changed");
        Tenant tenant = tenantMapper.selectById(user.getTenantId());
        require(tenant != null && enabled(tenant.getStatus()), "tenant is unavailable");
        require(claims.audience() == TrustedAudience.MINIAPP, "miniapp audience mismatch");
        TrustedExecutionContext context = new TrustedExecutionContext(
                TrustedPrincipalKind.ORGANIZATION_USER,
                LegacyIdentityUidFactory.principal("organization-user", user.getId()).value(),
                TrustedAudience.MINIAPP,
                LegacyIdentityUidFactory.tenant(user.getTenantId()).value(),
                LegacyIdentityUidFactory.organization(user.getTenantId()).value(),
                claims.jti(),
                0,
                "unassigned");
        return new ResolvedTrustedSession(
                context, user.getId(), user.getTenantId(), user.getRole(), user.getOpenid());
    }

    private static boolean enabled(Integer status) {
        return status == null || status != 0;
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new TrustedSessionRejectedException(message);
        }
    }
}
