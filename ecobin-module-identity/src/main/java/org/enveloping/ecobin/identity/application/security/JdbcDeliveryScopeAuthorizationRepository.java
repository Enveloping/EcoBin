package org.enveloping.ecobin.identity.application.security;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.LinkedHashSet;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

@Repository
class JdbcDeliveryScopeAuthorizationRepository
        implements DeliveryScopeAuthorizationRepository {

    private static final String CAPABILITY_FILTER = """
            AND p.permission_code IN (
                'delivery.read',
                'review.execute',
                'delivery.correct',
                'wallet.read'
            )
            """;

    private final JdbcTemplate jdbc;

    JdbcDeliveryScopeAuthorizationRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<PlatformActor> findPlatformActor(
            long platformAdminId,
            UUID platformAdminUid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, display_name, enabled, auth_version
                        FROM iam_platform_admin
                        WHERE id = ?
                          AND platform_admin_uid = ?
                        """ + lockClause(forUpdate),
                (rs, ignored) -> new PlatformActor(
                        rs.getLong("id"),
                        rs.getString("display_name"),
                        rs.getBoolean("enabled"),
                        rs.getLong("auth_version")),
                platformAdminId,
                platformAdminUid.toString()).stream().findFirst();
    }

    @Override
    public Optional<StaffActor> findStaffActor(
            long staffAccountId,
            UUID staffAccountUid,
            boolean forUpdate) {
        return jdbc.query("""
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
                        """ + lockClause(forUpdate),
                (rs, ignored) -> new StaffActor(
                        rs.getLong("id"),
                        rs.getLong("tenant_id"),
                        rs.getString("account_kind"),
                        rs.getString("display_name"),
                        rs.getBoolean("enabled"),
                        rs.getLong("auth_version"),
                        rs.getString("tenant_code"),
                        "ENABLED".equals(rs.getString("tenant_status"))),
                staffAccountId,
                staffAccountUid.toString()).stream().findFirst();
    }

    @Override
    public Optional<Scope> findPlatformScope(
            String tenantCode,
            String organizationCode,
            boolean forUpdate) {
        return findScope(tenantCode, null, organizationCode, forUpdate);
    }

    @Override
    public Optional<Scope> findStaffScope(
            long tenantId,
            String organizationCode,
            boolean forUpdate) {
        return findScope(null, tenantId, organizationCode, forUpdate);
    }

    @Override
    public Set<String> findTenantDeliveryCapabilities(
            long tenantId,
            long staffAccountId) {
        return capabilities(
                tenantId,
                null,
                staffAccountId,
                "TENANT");
    }

    @Override
    public Optional<Membership> findMembership(
            long tenantId,
            long organizationId,
            long staffAccountId) {
        return jdbc.query("""
                        SELECT is_manager, enabled
                        FROM iam_organization_staff_membership
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND staff_account_id = ?
                        """,
                (rs, ignored) -> new Membership(
                        rs.getBoolean("is_manager"),
                        rs.getBoolean("enabled")),
                tenantId,
                organizationId,
                staffAccountId).stream().findFirst();
    }

    @Override
    public Set<String> findOrganizationDeliveryCapabilities(
            long tenantId,
            long organizationId,
            long staffAccountId) {
        return capabilities(
                tenantId,
                organizationId,
                staffAccountId,
                "ORGANIZATION");
    }

    @Override
    public Optional<OrganizationUser> findOrganizationUser(
            long tenantId,
            long organizationId,
            UUID organizationUserUid) {
        return jdbc.query("""
                        SELECT id, organization_user_uid
                        FROM iam_organization_user
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND organization_user_uid = ?
                        """,
                (rs, ignored) -> new OrganizationUser(
                        rs.getLong("id"),
                        UUID.fromString(
                                rs.getString(
                                        "organization_user_uid"))),
                tenantId,
                organizationId,
                organizationUserUid.toString())
                .stream()
                .findFirst();
    }

    private Optional<Scope> findScope(
            String tenantCode,
            Long tenantId,
            String organizationCode,
            boolean forUpdate) {
        String predicate = tenantId == null
                ? "t.tenant_code = ?"
                : "t.id = ?";
        Object tenantValue = tenantId == null ? tenantCode : tenantId;
        return jdbc.query("""
                        SELECT t.id AS tenant_id,
                               t.tenant_code,
                               o.id AS organization_id,
                               o.organization_code,
                               o.status AS organization_status
                        FROM iam_tenant t
                        JOIN iam_organization o
                          ON o.tenant_id = t.id
                        WHERE %s
                          AND o.organization_code = ?
                        %s
                        """.formatted(
                        predicate,
                        lockClause(forUpdate)),
                (rs, ignored) -> new Scope(
                        rs.getLong("tenant_id"),
                        rs.getString("tenant_code"),
                        rs.getLong("organization_id"),
                        rs.getString("organization_code"),
                        "ENABLED".equals(
                                rs.getString("organization_status"))),
                tenantValue,
                organizationCode).stream().findFirst();
    }

    private Set<String> capabilities(
            long tenantId,
            Long organizationId,
            long staffAccountId,
            String scopeKind) {
        return new LinkedHashSet<>(jdbc.query("""
                        SELECT p.permission_code
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
                        """ + CAPABILITY_FILTER,
                (rs, ignored) -> rs.getString("permission_code"),
                tenantId,
                staffAccountId,
                scopeKind,
                organizationId,
                organizationId));
    }

    private static String lockClause(boolean forUpdate) {
        return forUpdate ? "FOR UPDATE" : "";
    }
}
