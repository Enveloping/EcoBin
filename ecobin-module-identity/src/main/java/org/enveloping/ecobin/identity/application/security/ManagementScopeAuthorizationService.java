package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.IdentityOwnedManagementScopePersistenceRefFactory;
import org.enveloping.ecobin.identity.api.port.ManagementScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedManagementScope;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActor;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActorContext;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Set;
import java.util.UUID;

import static org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery.Channel.MINIAPP_STAFF;

@Service
public class ManagementScopeAuthorizationService
        implements ManagementScopeAuthorizationPort {

    private static final Set<String> MINIAPP_STAFF_CAPABILITIES = Set.of(
            "statistics.read", "device.read", "alert.read");

    private final JdbcTemplate jdbc;
    private final IdentityOwnedManagementScopePersistenceRefFactory refs;

    public ManagementScopeAuthorizationService(
            JdbcTemplate jdbc,
            IdentityOwnedManagementScopePersistenceRefFactory refs) {
        this.jdbc = jdbc;
        this.refs = refs;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public AuthorizedManagementScope authorize(
            ManagementScopeAuthorizationQuery query) {
        return query.channel() == MINIAPP_STAFF
                ? authorizeMiniapp(query)
                : authorizeWeb(query);
    }

    private AuthorizedManagementScope authorizeWeb(
            ManagementScopeAuthorizationQuery query) {
        TargetWebActor actor = TargetWebActorContext.required();
        if (query.platformPath()) {
            if (!actor.platform()) {
                throw forbidden();
            }
            PlatformRow platform = jdbc.query("""
                            SELECT id, display_name
                            FROM iam_platform_admin
                            WHERE id = ?
                              AND platform_admin_uid = ?
                              AND enabled = 1
                              AND auth_version = ?
                            """,
                    (rs, ignored) -> new PlatformRow(
                            rs.getLong("id"),
                            rs.getString("display_name")),
                    actor.principalId(),
                    actor.principalUid().toString(),
                    actor.authVersion()).stream().findFirst()
                    .orElseThrow(ManagementScopeAuthorizationService::sessionInvalid);
            TenantRow tenant = query.tenantCode() == null
                    ? null : tenant(query.tenantCode());
            List<OrganizationRow> organizations = tenant == null
                    ? List.of()
                    : organizations(
                            tenant.id(), query.organizationCode(), null, true,
                            query.requiredCapability());
            requireRequestedOrganization(query, organizations);
            return authorized(
                    true,
                    actor.principalUid(),
                    actor.sessionUid(),
                    platform.displayName(),
                    tenant,
                    tenant != null,
                    organizations,
                    platform.id(),
                    null);
        }
        if (actor.platform()) {
            throw forbidden();
        }
        StaffRow staff = staff(actor.principalId(), actor.principalUid(),
                actor.authVersion());
        List<OrganizationRow> organizations = organizations(
                staff.tenantId(),
                query.organizationCode(),
                staff,
                false,
                query.requiredCapability());
        requireRequestedOrganization(query, organizations);
        if (organizations.isEmpty()) {
            throw forbidden();
        }
        return authorized(
                false,
                actor.principalUid(),
                actor.sessionUid(),
                staff.displayName(),
                new TenantRow(staff.tenantId(), staff.tenantCode()),
                "TENANT_PRINCIPAL".equals(staff.accountKind())
                        || hasTenantCapability(
                        staff.tenantId(), staff.id(),
                        query.requiredCapability()),
                organizations,
                null,
                staff.id());
    }

    private AuthorizedManagementScope authorizeMiniapp(
            ManagementScopeAuthorizationQuery query) {
        if (!MINIAPP_STAFF_CAPABILITIES.contains(
                query.requiredCapability())) {
            throw forbidden();
        }
        TargetMiniappActor actor = TargetMiniappActorContext.required();
        if (actor.audience() != TrustedAudience.MINIAPP_STAFF
                || actor.staffMiniappBindingId() == null) {
            throw forbidden();
        }
        List<MiniappRow> rows = jdbc.query("""
                        SELECT s.id AS staff_id,
                               s.account_kind,
                               s.display_name,
                               t.tenant_code,
                               o.organization_code,
                               o.organization_name
                        FROM iam_staff_account s
                        JOIN iam_tenant t
                          ON t.id = s.tenant_id
                         AND t.status = 'ENABLED'
                        JOIN iam_organization o
                          ON o.tenant_id = s.tenant_id
                         AND o.id = ?
                         AND o.status = 'ENABLED'
                        JOIN iam_organization_miniapp_binding ob
                          ON ob.tenant_id = o.tenant_id
                         AND ob.organization_id = o.id
                         AND ob.miniapp_channel_id = ?
                         AND ob.status = 'ACTIVE'
                        JOIN iam_miniapp_channel app
                          ON app.id = ob.miniapp_channel_id
                         AND app.login_enabled = 1
                        JOIN iam_staff_miniapp_binding binding
                          ON binding.tenant_id = o.tenant_id
                         AND binding.organization_id = o.id
                         AND binding.miniapp_channel_id = app.id
                         AND binding.id = ?
                         AND binding.staff_account_id = s.id
                         AND binding.status = 'ACTIVE'
                        WHERE s.id = ?
                          AND s.staff_account_uid = ?
                          AND s.enabled = 1
                          AND s.auth_version = ?
                        """,
                (rs, ignored) -> new MiniappRow(
                        rs.getLong("staff_id"),
                        rs.getString("account_kind"),
                        rs.getString("display_name"),
                        rs.getString("tenant_code"),
                        rs.getString("organization_code"),
                        rs.getString("organization_name")),
                actor.organizationId(),
                actor.miniappChannelId(),
                actor.staffMiniappBindingId(),
                actor.principalId(),
                actor.principalUid().toString(),
                actor.authVersion());
        if (rows.size() != 1) {
            throw sessionInvalid();
        }
        MiniappRow row = rows.getFirst();
        if (!"TENANT_PRINCIPAL".equals(row.accountKind())
                && !hasOrganizationCapability(
                actor.tenantId(), actor.organizationId(), row.staffId(),
                query.requiredCapability())) {
            throw forbidden();
        }
        return new AuthorizedManagementScope(
                false,
                actor.principalUid(),
                actor.sessionUid(),
                row.displayName(),
                row.tenantCode(),
                false,
                List.of(new AuthorizedManagementScope.Organization(
                        row.organizationCode(),
                        row.organizationName())),
                refs.issue(
                        actor.tenantId(),
                        List.of(actor.organizationId()),
                        List.of(row.organizationCode()),
                        null,
                        row.staffId()));
    }

    private TenantRow tenant(String tenantCode) {
        return jdbc.query("""
                        SELECT id, tenant_code
                        FROM iam_tenant
                        WHERE tenant_code = ?
                          AND status = 'ENABLED'
                        """,
                (rs, ignored) -> new TenantRow(
                        rs.getLong("id"), rs.getString("tenant_code")),
                tenantCode).stream().findFirst().orElseThrow(
                ManagementScopeAuthorizationService::notFound);
    }

    private StaffRow staff(long id, UUID uid, long authVersion) {
        return jdbc.query("""
                        SELECT s.id, s.tenant_id, s.account_kind,
                               s.display_name, t.tenant_code
                        FROM iam_staff_account s
                        JOIN iam_tenant t
                          ON t.id = s.tenant_id
                         AND t.status = 'ENABLED'
                        WHERE s.id = ?
                          AND s.staff_account_uid = ?
                          AND s.enabled = 1
                          AND s.auth_version = ?
                        """,
                (rs, ignored) -> new StaffRow(
                        rs.getLong("id"),
                        rs.getLong("tenant_id"),
                        rs.getString("account_kind"),
                        rs.getString("display_name"),
                        rs.getString("tenant_code")),
                id, uid.toString(), authVersion).stream().findFirst()
                .orElseThrow(ManagementScopeAuthorizationService::sessionInvalid);
    }

    private List<OrganizationRow> organizations(
            long tenantId,
            String organizationCode,
            StaffRow staff,
            boolean platform,
            String capability) {
        StringBuilder sql = new StringBuilder("""
                SELECT o.id, o.organization_code, o.organization_name
                FROM iam_organization o
                WHERE o.tenant_id = ?
                  AND o.status = 'ENABLED'
                """);
        if (organizationCode != null) {
            sql.append(" AND o.organization_code = ?");
        }
        if (!platform && !"TENANT_PRINCIPAL".equals(staff.accountKind())) {
            sql.append("""
                     AND (
                       EXISTS (
                         SELECT 1
                         FROM iam_staff_permission_grant g
                         JOIN iam_permission_definition p
                           ON p.id = g.permission_definition_id
                          AND p.scope_kind = g.scope_kind
                          AND p.enabled = 1
                          AND p.permission_code = ?
                         WHERE g.tenant_id = o.tenant_id
                           AND g.staff_account_id = ?
                           AND g.revoked_at IS NULL
                           AND (
                             (g.scope_kind = 'TENANT'
                              AND g.organization_id IS NULL)
                             OR (g.scope_kind = 'ORGANIZATION'
                                 AND g.organization_id = o.id)
                           )
                       )
                       OR EXISTS (
                         SELECT 1
                         FROM iam_organization_staff_membership m
                         WHERE m.tenant_id = o.tenant_id
                           AND m.organization_id = o.id
                           AND m.staff_account_id = ?
                           AND m.enabled = 1
                           AND m.is_manager = 1
                       )
                     )
                    """);
        }
        sql.append(" ORDER BY o.organization_code");
        var args = new java.util.ArrayList<Object>();
        args.add(tenantId);
        if (organizationCode != null) {
            args.add(organizationCode);
        }
        if (!platform && !"TENANT_PRINCIPAL".equals(staff.accountKind())) {
            args.add(capability);
            args.add(staff.id());
            args.add(staff.id());
        }
        return jdbc.query(sql.toString(), (rs, ignored) ->
                        new OrganizationRow(
                                rs.getLong("id"),
                                rs.getString("organization_code"),
                                rs.getString("organization_name")),
                args.toArray());
    }

    private boolean hasOrganizationCapability(
            long tenantId,
            long organizationId,
            long staffId,
            String capability) {
        Integer count = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM iam_staff_account s
                        WHERE s.tenant_id = ?
                          AND s.id = ?
                          AND (
                            EXISTS (
                              SELECT 1
                              FROM iam_organization_staff_membership m
                              WHERE m.tenant_id = s.tenant_id
                                AND m.organization_id = ?
                                AND m.staff_account_id = s.id
                                AND m.enabled = 1
                                AND m.is_manager = 1
                            )
                            OR EXISTS (
                              SELECT 1
                              FROM iam_staff_permission_grant g
                              JOIN iam_permission_definition p
                                ON p.id = g.permission_definition_id
                               AND p.scope_kind = g.scope_kind
                               AND p.enabled = 1
                               AND p.permission_code = ?
                              WHERE g.tenant_id = s.tenant_id
                                AND g.staff_account_id = s.id
                                AND g.revoked_at IS NULL
                                AND (
                                  (g.scope_kind = 'TENANT'
                                   AND g.organization_id IS NULL)
                                  OR (g.scope_kind = 'ORGANIZATION'
                                      AND g.organization_id = ?)
                                )
                            )
                          )
                        """,
                Integer.class,
                tenantId, staffId, organizationId, capability,
                organizationId);
        return count != null && count > 0;
    }

    private AuthorizedManagementScope authorized(
            boolean platform,
            UUID principalUid,
            UUID sessionUid,
            String displayName,
            TenantRow tenant,
            boolean tenantWide,
            List<OrganizationRow> organizations,
            Long platformId,
            Long staffId) {
        return new AuthorizedManagementScope(
                platform,
                principalUid,
                sessionUid,
                displayName,
                tenant == null ? null : tenant.code(),
                tenantWide,
                organizations.stream().map(row ->
                        new AuthorizedManagementScope.Organization(
                                row.code(), row.name())).toList(),
                refs.issue(
                        tenant == null ? null : tenant.id(),
                        organizations.stream().map(OrganizationRow::id).toList(),
                        organizations.stream().map(OrganizationRow::code).toList(),
                        platformId,
                        staffId));
    }

    private static void requireRequestedOrganization(
            ManagementScopeAuthorizationQuery query,
            List<OrganizationRow> organizations) {
        if (query.organizationCode() != null && organizations.isEmpty()) {
            throw notFound();
        }
    }

    private boolean hasTenantCapability(
            long tenantId, long staffId, String capability) {
        Integer count = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM iam_staff_permission_grant g
                        JOIN iam_permission_definition p
                          ON p.id = g.permission_definition_id
                         AND p.scope_kind = g.scope_kind
                         AND p.enabled = 1
                         AND p.permission_code = ?
                        WHERE g.tenant_id = ?
                          AND g.staff_account_id = ?
                          AND g.scope_kind = 'TENANT'
                          AND g.organization_id IS NULL
                          AND g.revoked_at IS NULL
                        """, Integer.class,
                capability, tenantId, staffId);
        return count != null && count > 0;
    }

    private static TargetApiException forbidden() {
        return new TargetApiException(
                403, "AUTH.CAPABILITY_REQUIRED",
                "当前账号缺少所需管理能力");
    }

    private static TargetApiException sessionInvalid() {
        return new TargetApiException(
                401, "AUTH.SESSION_INVALID", "当前会话已失效");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "RESOURCE.NOT_FOUND", "目标资源不存在");
    }

    private record PlatformRow(long id, String displayName) { }
    private record TenantRow(long id, String code) { }
    private record StaffRow(
            long id,
            long tenantId,
            String accountKind,
            String displayName,
            String tenantCode) { }
    private record OrganizationRow(long id, String code, String name) { }
    private record MiniappRow(
            long staffId,
            String accountKind,
            String displayName,
            String tenantCode,
            String organizationCode,
            String organizationName) { }
}
