package org.enveloping.ecobin.framework.context;

import java.util.Objects;
import java.util.UUID;

/**
 * 认证入口结合签名凭据与当前数据库事实后建立的不可变请求上下文。
 */
public record TrustedExecutionContext(
        TrustedPrincipalKind principalKind,
        UUID principalUid,
        TrustedAudience audience,
        UUID tenantUid,
        UUID activeOrganizationUid,
        UUID sessionJti,
        long authVersion,
        String requestId) {

    public TrustedExecutionContext {
        Objects.requireNonNull(principalKind, "principalKind");
        Objects.requireNonNull(principalUid, "principalUid");
        Objects.requireNonNull(audience, "audience");
        Objects.requireNonNull(sessionJti, "sessionJti");
        Objects.requireNonNull(requestId, "requestId");
        if (authVersion < 0) {
            throw new IllegalArgumentException("authVersion must not be negative");
        }
        switch (principalKind) {
            case PLATFORM_ADMIN -> {
                require(audience == TrustedAudience.WEB_PLATFORM,
                        "platform administrator requires WEB_PLATFORM audience");
                require(tenantUid == null && activeOrganizationUid == null,
                        "platform administrator must not carry tenant or organization scope");
            }
            case STAFF_ACCOUNT -> {
                require(audience == TrustedAudience.WEB_STAFF
                                || audience == TrustedAudience.MINIAPP_STAFF,
                        "staff account requires a staff audience");
                require(tenantUid != null, "staff account requires tenant scope");
                if (audience == TrustedAudience.MINIAPP_STAFF) {
                    require(activeOrganizationUid != null,
                            "miniapp staff requires an active organization");
                }
            }
            case ORGANIZATION_USER -> {
                require(audience == TrustedAudience.MINIAPP,
                        "organization user requires MINIAPP audience");
                require(tenantUid != null && activeOrganizationUid != null,
                        "organization user requires tenant and organization scope");
            }
        }
    }

    public TrustedExecutionContext withRequestId(String value) {
        return new TrustedExecutionContext(
                principalKind,
                principalUid,
                audience,
                tenantUid,
                activeOrganizationUid,
                sessionJti,
                authVersion,
                value);
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new IllegalArgumentException(message);
        }
    }
}
