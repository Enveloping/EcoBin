package org.enveloping.ecobin.identity.infrastructure.persistence.v1;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.security.TargetWebSessionClaims;
import org.enveloping.ecobin.identity.application.web.OrganizationAccess;
import org.enveloping.ecobin.identity.application.web.TargetSessionRejectedException;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.WebAccountType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowCallbackHandler;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

@Repository
public class TargetIdentitySessionRepository {

    private final JdbcTemplate jdbc;

    public TargetIdentitySessionRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<PlatformLoginPrincipal> findPlatformLogin(String loginName) {
        return jdbc.query("""
                        SELECT id, platform_admin_uid, login_name, password_hash,
                               display_name, enabled, failed_login_count,
                               locked_until, auth_version, lock_version
                        FROM iam_platform_admin
                        WHERE login_name = ?
                        FOR UPDATE
                        """,
                (rs, row) -> platformLogin(rs),
                loginName).stream().findFirst();
    }

    public Optional<StaffLoginPrincipal> findStaffLogin(String loginName) {
        return jdbc.query("""
                        SELECT s.id, s.staff_account_uid, s.tenant_id,
                               s.account_kind, s.login_name, s.password_hash,
                               s.display_name, s.enabled, s.failed_login_count,
                               s.locked_until, s.auth_version, s.lock_version,
                               t.tenant_code, t.enterprise_name,
                               t.status AS tenant_status
                        FROM iam_staff_account s
                        JOIN iam_tenant t ON t.id = s.tenant_id
                        WHERE s.login_name = ?
                        FOR UPDATE
                        """,
                (rs, row) -> staffLogin(rs),
                loginName).stream().findFirst();
    }

    public void recordPlatformLoginFailure(long principalId, Instant lockedUntil) {
        jdbc.update("""
                        UPDATE iam_platform_admin
                        SET failed_login_count = failed_login_count + 1,
                            locked_until = ?,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE id = ?
                        """,
                timestamp(lockedUntil), principalId);
    }

    public void recordStaffLoginFailure(long principalId, Instant lockedUntil) {
        jdbc.update("""
                        UPDATE iam_staff_account
                        SET failed_login_count = failed_login_count + 1,
                            locked_until = ?,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE id = ?
                        """,
                timestamp(lockedUntil), principalId);
    }

    public void createPlatformSession(
            PlatformLoginPrincipal principal,
            UUID sessionUid,
            Instant issuedAt,
            Instant expiresAt,
            byte[] loginIp,
            byte[] userAgentSha256) {
        jdbc.update("""
                        UPDATE iam_platform_admin
                        SET failed_login_count = 0,
                            locked_until = NULL,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE id = ?
                        """, principal.id());
        jdbc.update("""
                        INSERT INTO iam_platform_login_session (
                            session_uid, platform_admin_id, issued_at,
                            expires_at, revoked_at, revocation_reason,
                            login_ip, user_agent_sha256,
                            auth_version_snapshot, created_at
                        ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
                        """,
                sessionUid.toString(),
                principal.id(),
                timestamp(issuedAt),
                timestamp(expiresAt),
                loginIp,
                userAgentSha256,
                principal.authVersion(),
                timestamp(issuedAt));
    }

    public void createStaffSession(
            StaffLoginPrincipal principal,
            UUID sessionUid,
            Instant issuedAt,
            Instant expiresAt,
            byte[] loginIp,
            byte[] userAgentSha256) {
        jdbc.update("""
                        UPDATE iam_staff_account
                        SET failed_login_count = 0,
                            locked_until = NULL,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE id = ?
                        """, principal.id());
        jdbc.update("""
                        INSERT INTO iam_staff_login_session (
                            session_uid, tenant_id, staff_account_id,
                            client_kind, staff_miniapp_binding_id,
                            organization_miniapp_id, active_organization_id,
                            issued_at, expires_at, revoked_at,
                            revocation_reason, login_ip, user_agent_sha256,
                            auth_version_snapshot, created_at
                        ) VALUES (
                            ?, ?, ?, 'WEB', NULL, NULL, NULL, ?, ?, NULL,
                            NULL, ?, ?, ?, ?
                        )
                        """,
                sessionUid.toString(),
                principal.tenantId(),
                principal.id(),
                timestamp(issuedAt),
                timestamp(expiresAt),
                loginIp,
                userAgentSha256,
                principal.authVersion(),
                timestamp(issuedAt));
    }

    public TargetWebActor resolve(TargetWebSessionClaims claims) {
        return switch (claims.audience()) {
            case WEB_PLATFORM -> resolvePlatform(claims);
            case WEB_STAFF -> resolveStaff(claims);
            default -> throw rejected("unsupported target Web audience");
        };
    }

    public void revoke(TargetWebActor actor, String reason) {
        String table = actor.platform()
                ? "iam_platform_login_session"
                : "iam_staff_login_session";
        jdbc.update("""
                        UPDATE %s
                        SET revocation_reason = CASE
                                WHEN revoked_at IS NULL THEN ?
                                ELSE revocation_reason
                            END,
                            revoked_at = COALESCE(
                                revoked_at, UTC_TIMESTAMP(3))
                        WHERE session_uid = ?
                        """.formatted(table),
                reason, actor.sessionUid().toString());
    }

    public void revokeAllStaffSessions(long tenantId, long staffAccountId, String reason) {
        jdbc.update("""
                        UPDATE iam_staff_login_session
                        SET revoked_at = UTC_TIMESTAMP(3),
                            revocation_reason = ?
                        WHERE tenant_id = ?
                          AND staff_account_id = ?
                          AND revoked_at IS NULL
                        """, reason, tenantId, staffAccountId);
    }

    public void revokeAllTenantSessions(long tenantId, String reason) {
        jdbc.update("""
                        UPDATE iam_staff_login_session
                        SET revoked_at = UTC_TIMESTAMP(3),
                            revocation_reason = ?
                        WHERE tenant_id = ?
                          AND revoked_at IS NULL
                        """, reason, tenantId);
        jdbc.update("""
                        UPDATE iam_organization_user_session
                        SET revoked_at = UTC_TIMESTAMP(3),
                            revocation_reason = ?
                        WHERE tenant_id = ?
                          AND revoked_at IS NULL
                        """, reason, tenantId);
    }

    public void revokeOrganizationSessions(
            long tenantId,
            long organizationId,
            String reason) {
        jdbc.update("""
                        UPDATE iam_staff_login_session session
                        JOIN iam_staff_account account
                          ON account.tenant_id = session.tenant_id
                         AND account.id = session.staff_account_id
                        LEFT JOIN iam_organization_staff_membership membership
                          ON membership.tenant_id = session.tenant_id
                         AND membership.staff_account_id =
                             session.staff_account_id
                         AND membership.organization_id = ?
                        SET session.revoked_at = UTC_TIMESTAMP(3),
                            session.revocation_reason = ?
                        WHERE session.tenant_id = ?
                          AND (
                              account.account_kind = 'TENANT_PRINCIPAL'
                              OR membership.id IS NOT NULL
                          )
                          AND session.revoked_at IS NULL
                        """, organizationId, reason, tenantId);
        jdbc.update("""
                        UPDATE iam_organization_user_session
                        SET revoked_at = UTC_TIMESTAMP(3),
                            revocation_reason = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND revoked_at IS NULL
                        """, reason, tenantId, organizationId);
    }

    public void revokeOrganizationUserSessions(
            long tenantId,
            long organizationId,
            long organizationUserId,
            String reason) {
        jdbc.update("""
                        UPDATE iam_organization_user_session
                        SET revoked_at = UTC_TIMESTAMP(3),
                            revocation_reason = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND organization_user_id = ?
                          AND revoked_at IS NULL
                        """,
                reason, tenantId, organizationId, organizationUserId);
    }

    public void revokeMiniappBindingSessions(
            long bindingId,
            String reason) {
        jdbc.update("""
                        UPDATE iam_staff_login_session
                        SET revoked_at = UTC_TIMESTAMP(3),
                            revocation_reason = ?
                        WHERE staff_miniapp_binding_id = ?
                          AND client_kind = 'MINIAPP_MANAGEMENT'
                          AND revoked_at IS NULL
                        """,
                reason, bindingId);
    }

    private TargetWebActor resolvePlatform(TargetWebSessionClaims claims) {
        PlatformSessionRow row = jdbc.query("""
                        SELECT s.session_uid, s.issued_at, s.expires_at,
                               s.revoked_at, s.auth_version_snapshot,
                               a.id AS principal_id, a.platform_admin_uid,
                               a.display_name, a.enabled, a.auth_version,
                               a.lock_version
                        FROM iam_platform_login_session s
                        JOIN iam_platform_admin a
                          ON a.id = s.platform_admin_id
                        WHERE s.session_uid = ?
                        """,
                (rs, ignored) -> new PlatformSessionRow(
                        UUID.fromString(rs.getString("session_uid")),
                        instant(rs, "issued_at"),
                        instant(rs, "expires_at"),
                        nullableInstant(rs, "revoked_at"),
                        rs.getLong("auth_version_snapshot"),
                        rs.getLong("principal_id"),
                        UUID.fromString(rs.getString("platform_admin_uid")),
                        rs.getString("display_name"),
                        rs.getBoolean("enabled"),
                        rs.getLong("auth_version"),
                        rs.getLong("lock_version")),
                claims.sessionUid().toString()).stream().findFirst()
                .orElseThrow(() -> rejected("platform session does not exist"));
        require(row.principalUid().equals(claims.principalUid()),
                "platform session subject mismatch");
        validateSession(
                row.issuedAt(), row.expiresAt(), row.revokedAt(),
                row.authVersionSnapshot(), row.authVersion(), claims);
        require(row.enabled(), "platform account is disabled");
        Set<String> capabilities = new LinkedHashSet<>(allPermissionCodes());
        capabilities.add("platform-admin.read");
        capabilities.add("platform-admin.manage");
        return new TargetWebActor(
                WebAccountType.PLATFORM_ADMIN,
                TrustedAudience.WEB_PLATFORM,
                row.principalId(),
                row.principalUid(),
                null,
                null,
                null,
                row.sessionUid(),
                row.displayName(),
                null,
                row.version(),
                row.authVersion(),
                row.expiresAt(),
                capabilities,
                List.of());
    }

    private TargetWebActor resolveStaff(TargetWebSessionClaims claims) {
        StaffSessionRow row = jdbc.query("""
                        SELECT ss.session_uid, ss.issued_at, ss.expires_at,
                               ss.revoked_at, ss.auth_version_snapshot,
                               ss.client_kind, s.id AS principal_id,
                               s.staff_account_uid, s.account_kind,
                               s.display_name, s.contact_phone,
                               s.enabled, s.auth_version,
                               s.lock_version, t.id AS tenant_id,
                               t.tenant_code, t.enterprise_name,
                               t.status AS tenant_status
                        FROM iam_staff_login_session ss
                        JOIN iam_staff_account s
                          ON s.tenant_id = ss.tenant_id
                         AND s.id = ss.staff_account_id
                        JOIN iam_tenant t ON t.id = ss.tenant_id
                        WHERE ss.session_uid = ?
                        """,
                (rs, ignored) -> staffSession(rs),
                claims.sessionUid().toString()).stream().findFirst()
                .orElseThrow(() -> rejected("staff session does not exist"));
        require(row.principalUid().equals(claims.principalUid()),
                "staff session subject mismatch");
        validateSession(
                row.issuedAt(), row.expiresAt(), row.revokedAt(),
                row.authVersionSnapshot(), row.authVersion(), claims);
        require("WEB".equals(row.clientKind()),
                "staff session client kind mismatch");
        require(row.enabled(), "staff account is disabled");
        require("ENABLED".equals(row.tenantStatus()),
                "tenant is disabled");

        boolean principal = "TENANT_PRINCIPAL".equals(row.accountKind());
        Set<String> tenantCapabilities = principal
                ? permissionCodes("TENANT")
                : tenantGrants(row.tenantId(), row.principalId());
        List<OrganizationAccess> organizations = organizationAccesses(
                row.tenantId(), row.principalId(), principal, tenantCapabilities);
        return new TargetWebActor(
                principal
                        ? WebAccountType.TENANT_PRINCIPAL
                        : WebAccountType.STAFF,
                TrustedAudience.WEB_STAFF,
                row.principalId(),
                row.principalUid(),
                row.tenantId(),
                row.tenantCode(),
                row.tenantName(),
                row.sessionUid(),
                row.displayName(),
                row.contactPhone(),
                row.version(),
                row.authVersion(),
                row.expiresAt(),
                tenantCapabilities,
                organizations);
    }

    private List<OrganizationAccess> organizationAccesses(
            long tenantId,
            long principalId,
            boolean tenantPrincipal,
            Set<String> tenantCapabilities) {
        List<OrganizationRow> enabledOrganizations = jdbc.query("""
                        SELECT id, organization_code, organization_name
                        FROM iam_organization
                        WHERE tenant_id = ?
                          AND status = 'ENABLED'
                        ORDER BY organization_code
                        """,
                (rs, ignored) -> new OrganizationRow(
                        rs.getLong("id"),
                        rs.getString("organization_code"),
                        rs.getString("organization_name")),
                tenantId);
        Set<String> allOrganizationPermissions = permissionCodes("ORGANIZATION");
        Map<Long, MembershipAccess> membershipByOrganization = new HashMap<>();
        jdbc.query("""
                        SELECT organization_id, is_manager
                        FROM iam_organization_staff_membership
                        WHERE tenant_id = ?
                          AND staff_account_id = ?
                          AND enabled = 1
                        """,
                (RowCallbackHandler) rs -> membershipByOrganization.put(
                        rs.getLong("organization_id"),
                        new MembershipAccess(rs.getBoolean("is_manager"))),
                tenantId, principalId);
        Map<Long, Set<String>> directGrants = new HashMap<>();
        jdbc.query("""
                        SELECT g.organization_id, p.permission_code
                        FROM iam_staff_permission_grant g
                        JOIN iam_permission_definition p
                          ON p.id = g.permission_definition_id
                         AND p.scope_kind = g.scope_kind
                        WHERE g.tenant_id = ?
                          AND g.staff_account_id = ?
                          AND g.scope_kind = 'ORGANIZATION'
                          AND g.revoked_at IS NULL
                          AND p.enabled = 1
                        """,
                (RowCallbackHandler) rs -> directGrants
                        .computeIfAbsent(
                                rs.getLong("organization_id"),
                                ignored -> new LinkedHashSet<>())
                        .add(rs.getString("permission_code")),
                tenantId, principalId);

        List<OrganizationAccess> result = new ArrayList<>();
        for (OrganizationRow organization : enabledOrganizations) {
            MembershipAccess membership =
                    membershipByOrganization.get(organization.id());
            if (!tenantPrincipal
                    && tenantCapabilities.isEmpty()
                    && membership == null) {
                continue;
            }
            boolean manager = tenantPrincipal
                    || (membership != null && membership.manager());
            LinkedHashSet<String> capabilities =
                    new LinkedHashSet<>(tenantCapabilities);
            if (manager) {
                capabilities.addAll(allOrganizationPermissions);
            } else {
                capabilities.addAll(directGrants.getOrDefault(
                        organization.id(), Set.of()));
            }
            result.add(new OrganizationAccess(
                    organization.id(),
                    organization.code(),
                    organization.name(),
                    manager,
                    capabilities));
        }
        result.sort(Comparator.comparing(OrganizationAccess::organizationCode));
        return List.copyOf(result);
    }

    private Set<String> allPermissionCodes() {
        return Set.copyOf(jdbc.queryForList("""
                SELECT DISTINCT permission_code
                FROM iam_permission_definition
                WHERE enabled = 1
                ORDER BY permission_code
                """, String.class));
    }

    private Set<String> permissionCodes(String scopeKind) {
        return Set.copyOf(jdbc.queryForList("""
                        SELECT permission_code
                        FROM iam_permission_definition
                        WHERE enabled = 1
                          AND scope_kind = ?
                        ORDER BY permission_code
                        """,
                String.class, scopeKind));
    }

    private Set<String> tenantGrants(long tenantId, long staffAccountId) {
        return Set.copyOf(jdbc.queryForList("""
                        SELECT p.permission_code
                        FROM iam_staff_permission_grant g
                        JOIN iam_permission_definition p
                          ON p.id = g.permission_definition_id
                         AND p.scope_kind = g.scope_kind
                        WHERE g.tenant_id = ?
                          AND g.staff_account_id = ?
                          AND g.scope_kind = 'TENANT'
                          AND g.organization_id IS NULL
                          AND g.revoked_at IS NULL
                          AND p.enabled = 1
                        ORDER BY p.permission_code
                        """,
                String.class, tenantId, staffAccountId));
    }

    private static void validateSession(
            Instant issuedAt,
            Instant expiresAt,
            Instant revokedAt,
            long snapshot,
            long currentAuthVersion,
            TargetWebSessionClaims claims) {
        require(revokedAt == null, "session is revoked");
        require(expiresAt.isAfter(Instant.now()), "session is expired");
        require(snapshot == currentAuthVersion,
                "session authorization version is stale");
        require(issuedAt.equals(claims.issuedAt()),
                "session issued-at mismatch");
        require(expiresAt.equals(claims.expiresAt()),
                "session expiry mismatch");
    }

    private static PlatformLoginPrincipal platformLogin(ResultSet rs)
            throws SQLException {
        return new PlatformLoginPrincipal(
                rs.getLong("id"),
                UUID.fromString(rs.getString("platform_admin_uid")),
                rs.getString("login_name"),
                rs.getString("password_hash"),
                rs.getString("display_name"),
                rs.getBoolean("enabled"),
                rs.getInt("failed_login_count"),
                nullableInstant(rs, "locked_until"),
                rs.getLong("auth_version"),
                rs.getLong("lock_version"));
    }

    private static StaffLoginPrincipal staffLogin(ResultSet rs)
            throws SQLException {
        return new StaffLoginPrincipal(
                rs.getLong("id"),
                UUID.fromString(rs.getString("staff_account_uid")),
                rs.getLong("tenant_id"),
                rs.getString("tenant_code"),
                rs.getString("enterprise_name"),
                rs.getString("tenant_status"),
                rs.getString("account_kind"),
                rs.getString("login_name"),
                rs.getString("password_hash"),
                rs.getString("display_name"),
                rs.getBoolean("enabled"),
                rs.getInt("failed_login_count"),
                nullableInstant(rs, "locked_until"),
                rs.getLong("auth_version"),
                rs.getLong("lock_version"));
    }

    private static StaffSessionRow staffSession(ResultSet rs)
            throws SQLException {
        return new StaffSessionRow(
                UUID.fromString(rs.getString("session_uid")),
                instant(rs, "issued_at"),
                instant(rs, "expires_at"),
                nullableInstant(rs, "revoked_at"),
                rs.getLong("auth_version_snapshot"),
                rs.getString("client_kind"),
                rs.getLong("principal_id"),
                UUID.fromString(rs.getString("staff_account_uid")),
                rs.getString("account_kind"),
                rs.getString("display_name"),
                rs.getString("contact_phone"),
                rs.getBoolean("enabled"),
                rs.getLong("auth_version"),
                rs.getLong("lock_version"),
                rs.getLong("tenant_id"),
                rs.getString("tenant_code"),
                rs.getString("enterprise_name"),
                rs.getString("tenant_status"));
    }

    private static LocalDateTime timestamp(Instant value) {
        return value == null
                ? null
                : LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }

    private static Instant instant(ResultSet rs, String column)
            throws SQLException {
        return rs.getObject(column, LocalDateTime.class)
                .toInstant(ZoneOffset.UTC);
    }

    private static Instant nullableInstant(ResultSet rs, String column)
            throws SQLException {
        LocalDateTime value = rs.getObject(column, LocalDateTime.class);
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static TargetSessionRejectedException rejected(String message) {
        return new TargetSessionRejectedException(message);
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw rejected(message);
        }
    }

    public record PlatformLoginPrincipal(
            long id,
            UUID principalUid,
            String loginName,
            String passwordHash,
            String displayName,
            boolean enabled,
            int failedLoginCount,
            Instant lockedUntil,
            long authVersion,
            long version) {
    }

    public record StaffLoginPrincipal(
            long id,
            UUID principalUid,
            long tenantId,
            String tenantCode,
            String tenantName,
            String tenantStatus,
            String accountKind,
            String loginName,
            String passwordHash,
            String displayName,
            boolean enabled,
            int failedLoginCount,
            Instant lockedUntil,
            long authVersion,
            long version) {
    }

    private record PlatformSessionRow(
            UUID sessionUid,
            Instant issuedAt,
            Instant expiresAt,
            Instant revokedAt,
            long authVersionSnapshot,
            long principalId,
            UUID principalUid,
            String displayName,
            boolean enabled,
            long authVersion,
            long version) {
    }

    private record StaffSessionRow(
            UUID sessionUid,
            Instant issuedAt,
            Instant expiresAt,
            Instant revokedAt,
            long authVersionSnapshot,
            String clientKind,
            long principalId,
            UUID principalUid,
            String accountKind,
            String displayName,
            String contactPhone,
            boolean enabled,
            long authVersion,
            long version,
            long tenantId,
            String tenantCode,
            String tenantName,
            String tenantStatus) {
    }

    private record OrganizationRow(long id, String code, String name) {
    }

    private record MembershipAccess(boolean manager) {
    }
}
