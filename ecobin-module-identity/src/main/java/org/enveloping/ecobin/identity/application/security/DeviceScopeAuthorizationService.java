package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.DeviceScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeviceScope;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;
import java.util.Map;

/**
 * Revalidates the authenticated actor, target scope and current capability in
 * the caller's transaction.
 */
@Service
public class DeviceScopeAuthorizationService
        implements DeviceScopeAuthorizationPort {

    private static final List<String> DEVICE_CAPABILITIES = List.of(
            "device.read",
            "device.manage",
            "device.configuration.manage");

    private final JdbcTemplate jdbc;
    private final DeviceScopePersistenceRefFactory referenceFactory;

    public DeviceScopeAuthorizationService(
            JdbcTemplate jdbc,
            DeviceScopePersistenceRefFactory referenceFactory) {
        this.jdbc = jdbc;
        this.referenceFactory = referenceFactory;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public AuthorizedDeviceScope authorize(
            DeviceScopeAuthorizationQuery query) {
        TargetWebActor actor = TargetWebActorContext.required();
        if (query.platformPath()) {
            return authorizePlatform(actor, query);
        }
        return authorizeStaff(actor, query);
    }

    private AuthorizedDeviceScope authorizePlatform(
            TargetWebActor actor,
            DeviceScopeAuthorizationQuery query) {
        if (!actor.platform()) {
            throw forbidden();
        }
        List<PlatformActorRow> actors = jdbc.query("""
                        SELECT id, display_name, enabled, auth_version
                        FROM iam_platform_admin
                        WHERE id = ?
                          AND platform_admin_uid = ?
                        """ + lockClause(),
                (rs, ignored) -> new PlatformActorRow(
                        rs.getLong("id"),
                        rs.getString("display_name"),
                        rs.getBoolean("enabled"),
                        rs.getLong("auth_version")),
                actor.principalId(),
                actor.principalUid().toString());
        if (actors.size() != 1
                || !actors.getFirst().enabled()
                || actors.getFirst().authVersion() != actor.authVersion()) {
            throw sessionInvalid();
        }

        ScopeRow scope = platformScope(query);
        return new AuthorizedDeviceScope(
                true,
                actor.principalUid(),
                actor.sessionUid(),
                actors.getFirst().displayName(),
                scope == null ? null : scope.tenantCode(),
                scope == null ? null : scope.organizationCode(),
                scope == null || scope.tenantEnabled(),
                scope == null || scope.organizationEnabled(),
                referenceFactory.issue(
                        scope == null ? null : scope.tenantId(),
                        scope == null ? null : scope.organizationId(),
                        actors.getFirst().id(),
                        null));
    }

    private ScopeRow platformScope(DeviceScopeAuthorizationQuery query) {
        if (query.tenantCode() == null && query.organizationCode() == null) {
            return null;
        }
        if (query.tenantCode() == null || query.organizationCode() == null) {
            throw invalidScope();
        }
        List<ScopeRow> rows = jdbc.query("""
                        SELECT t.id AS tenant_id,
                               t.tenant_code,
                               t.status AS tenant_status,
                               o.id AS organization_id,
                               o.organization_code,
                               o.status AS organization_status
                        FROM iam_tenant t
                        JOIN iam_organization o
                          ON o.tenant_id = t.id
                        WHERE t.tenant_code = ?
                          AND o.organization_code = ?
                        """,
                (rs, ignored) -> scopeRow(rs),
                query.tenantCode(),
                query.organizationCode());
        if (rows.isEmpty()) {
            throw notFound();
        }
        return rows.getFirst();
    }

    private AuthorizedDeviceScope authorizeStaff(
            TargetWebActor actor,
            DeviceScopeAuthorizationQuery query) {
        if (actor.platform()) {
            throw forbidden();
        }
        if (query.organizationCode() == null) {
            throw invalidScope();
        }
        List<StaffActorRow> actors = jdbc.query("""
                        SELECT s.id,
                               s.tenant_id,
                               s.account_kind,
                               s.display_name,
                               s.enabled,
                               s.auth_version,
                               t.tenant_code,
                               t.status AS tenant_status
                        FROM iam_staff_account s
                        JOIN iam_tenant t ON t.id = s.tenant_id
                        WHERE s.id = ?
                          AND s.staff_account_uid = ?
                        """ + lockClause(),
                (rs, ignored) -> new StaffActorRow(
                        rs.getLong("id"),
                        rs.getLong("tenant_id"),
                        rs.getString("account_kind"),
                        rs.getString("display_name"),
                        rs.getBoolean("enabled"),
                        rs.getLong("auth_version"),
                        rs.getString("tenant_code"),
                        "ENABLED".equals(rs.getString("tenant_status"))),
                actor.principalId(),
                actor.principalUid().toString());
        if (actors.size() != 1
                || !actors.getFirst().enabled()
                || actors.getFirst().authVersion() != actor.authVersion()) {
            throw sessionInvalid();
        }
        StaffActorRow current = actors.getFirst();
        List<ScopeRow> scopes = jdbc.query("""
                        SELECT t.id AS tenant_id,
                               t.tenant_code,
                               t.status AS tenant_status,
                               o.id AS organization_id,
                               o.organization_code,
                               o.status AS organization_status
                        FROM iam_tenant t
                        JOIN iam_organization o
                          ON o.tenant_id = t.id
                        WHERE t.id = ?
                          AND o.organization_code = ?
                        """,
                (rs, ignored) -> scopeRow(rs),
                current.tenantId(),
                query.organizationCode());
        if (scopes.isEmpty()) {
            throw notFound();
        }
        ScopeRow scope = scopes.getFirst();
        Access access = access(
                current,
                scope.organizationId(),
                query.requiredCapability());
        if (!access.visible()) {
            throw notFound();
        }
        if (!access.allowed()) {
            throw forbidden();
        }
        return new AuthorizedDeviceScope(
                false,
                actor.principalUid(),
                actor.sessionUid(),
                current.displayName(),
                current.tenantCode(),
                scope.organizationCode(),
                current.tenantEnabled(),
                scope.organizationEnabled(),
                referenceFactory.issue(
                        current.tenantId(),
                        scope.organizationId(),
                        null,
                        current.id()));
    }

    private Access access(
            StaffActorRow actor,
            long organizationId,
            String capability) {
        if ("TENANT_PRINCIPAL".equals(actor.accountKind())) {
            return new Access(true, true);
        }
        boolean tenantAllowed = hasGrant(
                actor.tenantId(), actor.id(), null, "TENANT", capability);
        boolean tenantVisible = DEVICE_CAPABILITIES.stream().anyMatch(code ->
                hasGrant(
                        actor.tenantId(),
                        actor.id(),
                        null,
                        "TENANT",
                        code));
        List<MembershipRow> memberships = jdbc.query("""
                        SELECT is_manager, enabled
                        FROM iam_organization_staff_membership
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND staff_account_id = ?
                        """,
                (rs, ignored) -> new MembershipRow(
                        rs.getBoolean("is_manager"),
                        rs.getBoolean("enabled")),
                actor.tenantId(),
                organizationId,
                actor.id());
        MembershipRow membership = memberships.isEmpty()
                ? null : memberships.getFirst();
        boolean member = membership != null && membership.enabled();
        boolean organizationAllowed = member
                && (membership.manager()
                || hasGrant(
                        actor.tenantId(),
                        actor.id(),
                        organizationId,
                        "ORGANIZATION",
                        capability));
        return new Access(
                tenantVisible || member,
                tenantAllowed || organizationAllowed);
    }

    private boolean hasGrant(
            long tenantId,
            long staffId,
            Long organizationId,
            String scopeKind,
            String capability) {
        Integer count = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM iam_staff_permission_grant g
                        JOIN iam_permission_definition p
                          ON p.id = g.permission_definition_id
                         AND p.scope_kind = g.scope_kind
                        WHERE g.tenant_id = ?
                          AND g.staff_account_id = ?
                          AND g.scope_kind = ?
                          AND ((? IS NULL AND g.organization_id IS NULL)
                               OR g.organization_id = ?)
                          AND g.revoked_at IS NULL
                          AND p.enabled = 1
                          AND p.permission_code = ?
                        """,
                Integer.class,
                tenantId,
                staffId,
                scopeKind,
                organizationId,
                organizationId,
                capability);
        return count != null && count > 0;
    }

    private static String lockClause() {
        return TransactionSynchronizationManager.isCurrentTransactionReadOnly()
                ? ""
                : " FOR UPDATE";
    }

    private static ScopeRow scopeRow(java.sql.ResultSet rs)
            throws java.sql.SQLException {
        return new ScopeRow(
                rs.getLong("tenant_id"),
                rs.getString("tenant_code"),
                "ENABLED".equals(rs.getString("tenant_status")),
                rs.getLong("organization_id"),
                rs.getString("organization_code"),
                "ENABLED".equals(rs.getString("organization_status")));
    }

    private static TargetApiException sessionInvalid() {
        return new TargetApiException(
                401,
                "AUTH.SESSION_INVALID",
                "当前 Web 会话已失效");
    }

    private static TargetApiException forbidden() {
        return new TargetApiException(
                403,
                "AUTH.CAPABILITY_REQUIRED",
                "当前账号缺少所需设备能力");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "COMMON.RESOURCE_NOT_FOUND",
                "目标租户或机构不存在于可见范围");
    }

    private static TargetApiException invalidScope() {
        return new TargetApiException(
                400,
                "COMMON.INVALID_REQUEST",
                "设备目标路径缺少完整租户或机构标识",
                false,
                Map.of());
    }

    private record PlatformActorRow(
            long id,
            String displayName,
            boolean enabled,
            long authVersion) {
    }

    private record StaffActorRow(
            long id,
            long tenantId,
            String accountKind,
            String displayName,
            boolean enabled,
            long authVersion,
            String tenantCode,
            boolean tenantEnabled) {
    }

    private record ScopeRow(
            long tenantId,
            String tenantCode,
            boolean tenantEnabled,
            long organizationId,
            String organizationCode,
            boolean organizationEnabled) {
    }

    private record MembershipRow(boolean manager, boolean enabled) {
    }

    private record Access(boolean visible, boolean allowed) {
    }
}
