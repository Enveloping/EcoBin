package org.enveloping.ecobin.framework.security;

import org.enveloping.ecobin.framework.context.TrustedAudience;

import java.time.Instant;
import java.util.UUID;

/**
 * 已验签 JWT 的迁移期最小声明；它仍须由 identity 结合当前数据库事实解析。
 */
public record JwtSessionClaims(
        long legacyPrincipalId,
        long legacyTenantId,
        int legacyRole,
        String subject,
        UUID jti,
        TrustedAudience audience,
        Instant issuedAt) {
}
