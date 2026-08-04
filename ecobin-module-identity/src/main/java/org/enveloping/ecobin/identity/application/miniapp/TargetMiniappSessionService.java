package org.enveloping.ecobin.identity.application.miniapp;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.security.TargetMiniappSessionClaims;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.MiniappSessionView;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.OrganizationSummary;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

@Service
public class TargetMiniappSessionService {

    private static final Set<String> MANAGEMENT_ALLOWLIST =
            Set.of("statistics.read", "device.read", "alert.read");

    private final JdbcTemplate jdbc;
    private final JwtTokenProvider tokenProvider;

    public TargetMiniappSessionService(
            JdbcTemplate jdbc,
            JwtTokenProvider tokenProvider) {
        this.jdbc = jdbc;
        this.tokenProvider = tokenProvider;
    }

    @Transactional(readOnly = true)
    public TargetMiniappActor resolve(String token) {
        try {
            TargetMiniappSessionClaims claims =
                    tokenProvider.parseTargetMiniappSession(token);
            return claims.audience() == TrustedAudience.MINIAPP
                    ? resolveOrganizationUser(claims)
                    : resolveStaff(claims);
        } catch (TargetApiException exception) {
            throw exception;
        } catch (RuntimeException exception) {
            throw rejected();
        }
    }

    @Transactional
    public void revokeCurrent(TargetMiniappActor actor) {
        if (actor.audience() == TrustedAudience.MINIAPP) {
            jdbc.update("""
                            UPDATE iam_organization_user_session
                            SET revoked_at = UTC_TIMESTAMP(3),
                                revocation_reason = 'USER_LOGOUT'
                            WHERE session_uid = ?
                              AND revoked_at IS NULL
                            """,
                    actor.sessionUid().toString());
            return;
        }
        jdbc.update("""
                        UPDATE iam_staff_login_session
                        SET revoked_at = UTC_TIMESTAMP(3),
                            revocation_reason = 'USER_LOGOUT'
                        WHERE session_uid = ?
                          AND client_kind = 'MINIAPP_MANAGEMENT'
                          AND revoked_at IS NULL
                        """,
                actor.sessionUid().toString());
    }

    public MiniappSessionView view(TargetMiniappActor actor) {
        return new MiniappSessionView(
                audienceName(actor.audience()),
                actor.entryMode(),
                actor.expiresAt(),
                new OrganizationSummary(
                        actor.organizationCode(),
                        actor.organizationName()),
                actor.principalUid(),
                actor.displayName(),
                actor.capabilities(),
                actor.phoneBound());
    }

    private TargetMiniappActor resolveOrganizationUser(
            TargetMiniappSessionClaims claims) {
        OrdinarySessionRow row = jdbc.query("""
                        SELECT s.session_uid, s.issued_at, s.expires_at,
                               s.revoked_at, s.auth_version_snapshot,
                               u.id AS user_id, u.organization_user_uid,
                               u.phone_e164, u.phone_bound_at, u.nickname,
                               u.status AS user_status,
                               u.auth_version AS user_auth_version,
                               t.id AS tenant_id, t.tenant_code,
                               t.status AS tenant_status,
                               o.id AS organization_id,
                               o.organization_code,
                               o.organization_name,
                               o.status AS organization_status,
                               m.id AS miniapp_id, m.appid,
                               m.login_enabled, m.activated_at
                        FROM iam_organization_user_session s
                        JOIN iam_organization_user u
                          ON u.tenant_id = s.tenant_id
                         AND u.organization_id = s.organization_id
                         AND u.organization_miniapp_id =
                             s.organization_miniapp_id
                         AND u.id = s.organization_user_id
                        JOIN iam_organization_miniapp m
                          ON m.tenant_id = s.tenant_id
                         AND m.organization_id = s.organization_id
                         AND m.id = s.organization_miniapp_id
                        JOIN iam_tenant t ON t.id = s.tenant_id
                        JOIN iam_organization o
                          ON o.tenant_id = s.tenant_id
                         AND o.id = s.organization_id
                        WHERE s.session_uid = ?
                        """,
                (rs, ignored) -> ordinarySessionRow(rs),
                claims.sessionUid().toString())
                .stream().findFirst().orElseThrow(
                        TargetMiniappSessionService::rejected);
        validateCommon(
                claims,
                row.sessionUid(),
                row.userUid(),
                row.issuedAt(),
                row.expiresAt(),
                row.revokedAt(),
                row.authVersionSnapshot(),
                row.userAuthVersion(),
                row.tenantStatus(),
                row.organizationStatus(),
                row.loginEnabled(),
                row.activatedAt());
        require("ACTIVE".equals(row.userStatus()));
        boolean cleaner = hasCleanerCapability(
                row.tenantId(), row.organizationId(), row.userId());
        return new TargetMiniappActor(
                TrustedPrincipalKind.ORGANIZATION_USER,
                TrustedAudience.MINIAPP,
                row.userId(),
                row.userUid(),
                row.tenantId(),
                row.tenantCode(),
                row.organizationId(),
                row.organizationCode(),
                row.organizationName(),
                row.miniappId(),
                row.appId(),
                row.userId(),
                null,
                row.sessionUid(),
                row.userAuthVersion(),
                row.expiresAt(),
                cleaner ? "CLEANING" : "USER",
                displayName(row.nickname()),
                cleaner ? List.of("clean.operation") : List.of(),
                row.phoneE164(),
                row.phoneBoundAt());
    }

    private TargetMiniappActor resolveStaff(
            TargetMiniappSessionClaims claims) {
        StaffSessionRow row = jdbc.query("""
                        SELECT s.session_uid, s.issued_at, s.expires_at,
                               s.revoked_at, s.auth_version_snapshot,
                               s.client_kind,
                               staff.id AS staff_id,
                               staff.staff_account_uid,
                               staff.account_kind,
                               staff.display_name,
                               staff.enabled AS staff_enabled,
                               staff.auth_version AS staff_auth_version,
                               b.id AS binding_id, b.status AS binding_status,
                               u.id AS user_id, u.phone_e164,
                               u.phone_bound_at,
                               t.id AS tenant_id, t.tenant_code,
                               t.status AS tenant_status,
                               o.id AS organization_id,
                               o.organization_code,
                               o.organization_name,
                               o.status AS organization_status,
                               m.id AS miniapp_id, m.appid,
                               m.login_enabled, m.activated_at
                        FROM iam_staff_login_session s
                        JOIN iam_staff_account staff
                          ON staff.tenant_id = s.tenant_id
                         AND staff.id = s.staff_account_id
                        JOIN iam_staff_miniapp_binding b
                          ON b.tenant_id = s.tenant_id
                         AND b.organization_id =
                             s.active_organization_id
                         AND b.organization_miniapp_id =
                             s.organization_miniapp_id
                         AND b.staff_account_id = s.staff_account_id
                         AND b.id = s.staff_miniapp_binding_id
                        JOIN iam_organization_user u
                          ON u.tenant_id = b.tenant_id
                         AND u.organization_id = b.organization_id
                         AND u.organization_miniapp_id =
                             b.organization_miniapp_id
                         AND u.id = b.organization_user_id
                        JOIN iam_organization_miniapp m
                          ON m.tenant_id = b.tenant_id
                         AND m.organization_id = b.organization_id
                         AND m.id = b.organization_miniapp_id
                        JOIN iam_tenant t ON t.id = b.tenant_id
                        JOIN iam_organization o
                          ON o.tenant_id = b.tenant_id
                         AND o.id = b.organization_id
                        WHERE s.session_uid = ?
                        """,
                (rs, ignored) -> staffSessionRow(rs),
                claims.sessionUid().toString())
                .stream().findFirst().orElseThrow(
                        TargetMiniappSessionService::rejected);
        validateCommon(
                claims,
                row.sessionUid(),
                row.staffUid(),
                row.issuedAt(),
                row.expiresAt(),
                row.revokedAt(),
                row.authVersionSnapshot(),
                row.staffAuthVersion(),
                row.tenantStatus(),
                row.organizationStatus(),
                row.loginEnabled(),
                row.activatedAt());
        require("MINIAPP_MANAGEMENT".equals(row.clientKind()));
        require(row.staffEnabled());
        require("ACTIVE".equals(row.bindingStatus()));
        require(row.phoneE164() != null && row.phoneBoundAt() != null);

        boolean principal =
                "TENANT_PRINCIPAL".equals(row.accountKind());
        boolean manager = enabledManagerMembership(
                row.tenantId(), row.organizationId(), row.staffId());
        Set<String> grants = effectiveManagementGrants(
                row.tenantId(), row.organizationId(), row.staffId());
        require(principal || manager || !grants.isEmpty());
        List<String> capabilities = principal || manager
                ? MANAGEMENT_ALLOWLIST.stream().sorted().toList()
                : grants.stream()
                    .filter(MANAGEMENT_ALLOWLIST::contains)
                    .sorted()
                    .toList();
        return new TargetMiniappActor(
                TrustedPrincipalKind.STAFF_ACCOUNT,
                TrustedAudience.MINIAPP_STAFF,
                row.staffId(),
                row.staffUid(),
                row.tenantId(),
                row.tenantCode(),
                row.organizationId(),
                row.organizationCode(),
                row.organizationName(),
                row.miniappId(),
                row.appId(),
                row.userId(),
                row.bindingId(),
                row.sessionUid(),
                row.staffAuthVersion(),
                row.expiresAt(),
                "MANAGEMENT",
                row.displayName(),
                capabilities,
                row.phoneE164(),
                row.phoneBoundAt());
    }

    private void validateCommon(
            TargetMiniappSessionClaims claims,
            UUID sessionUid,
            UUID principalUid,
            Instant issuedAt,
            Instant expiresAt,
            Instant revokedAt,
            long authSnapshot,
            long currentAuthVersion,
            String tenantStatus,
            String organizationStatus,
            boolean loginEnabled,
            Instant activatedAt) {
        require(sessionUid.equals(claims.sessionUid()));
        require(principalUid.equals(claims.principalUid()));
        require(issuedAt.equals(claims.issuedAt()));
        require(expiresAt.equals(claims.expiresAt()));
        require(expiresAt.isAfter(Instant.now()));
        require(revokedAt == null);
        require(authSnapshot == currentAuthVersion);
        require("ENABLED".equals(tenantStatus));
        require("ENABLED".equals(organizationStatus));
        require(loginEnabled && activatedAt != null);
    }

    private boolean hasCleanerCapability(
            long tenantId,
            long organizationId,
            long userId) {
        return Boolean.TRUE.equals(jdbc.query("""
                        SELECT enabled
                        FROM iam_organization_user_capability
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND organization_user_id = ?
                          AND capability_code = 'CLEAN_OPERATION'
                        """,
                (rs, ignored) -> rs.getBoolean("enabled"),
                tenantId, organizationId, userId)
                .stream().findFirst().orElse(false));
    }

    private boolean enabledManagerMembership(
            long tenantId,
            long organizationId,
            long staffId) {
        return Boolean.TRUE.equals(jdbc.query("""
                        SELECT is_manager
                        FROM iam_organization_staff_membership
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND staff_account_id = ?
                          AND enabled = 1
                        """,
                (rs, ignored) -> rs.getBoolean("is_manager"),
                tenantId, organizationId, staffId)
                .stream().findFirst().orElse(false));
    }

    private Set<String> effectiveManagementGrants(
            long tenantId,
            long organizationId,
            long staffId) {
        return new LinkedHashSet<>(jdbc.queryForList("""
                        SELECT DISTINCT p.permission_code
                        FROM iam_staff_permission_grant g
                        JOIN iam_permission_definition p
                          ON p.id = g.permission_definition_id
                         AND p.scope_kind = g.scope_kind
                        WHERE g.tenant_id = ?
                          AND g.staff_account_id = ?
                          AND g.revoked_at IS NULL
                          AND p.enabled = 1
                          AND (
                              (g.scope_kind = 'TENANT'
                               AND g.organization_id IS NULL)
                              OR
                              (g.scope_kind = 'ORGANIZATION'
                               AND g.organization_id = ?)
                          )
                        """,
                String.class,
                tenantId,
                staffId,
                organizationId));
    }

    private static OrdinarySessionRow ordinarySessionRow(ResultSet rs)
            throws SQLException {
        return new OrdinarySessionRow(
                UUID.fromString(rs.getString("session_uid")),
                instant(rs, "issued_at"),
                instant(rs, "expires_at"),
                nullableInstant(rs, "revoked_at"),
                rs.getLong("auth_version_snapshot"),
                rs.getLong("user_id"),
                UUID.fromString(rs.getString("organization_user_uid")),
                rs.getString("phone_e164"),
                nullableInstant(rs, "phone_bound_at"),
                rs.getString("nickname"),
                rs.getString("user_status"),
                rs.getLong("user_auth_version"),
                rs.getLong("tenant_id"),
                rs.getString("tenant_code"),
                rs.getString("tenant_status"),
                rs.getLong("organization_id"),
                rs.getString("organization_code"),
                rs.getString("organization_name"),
                rs.getString("organization_status"),
                rs.getLong("miniapp_id"),
                rs.getString("appid"),
                rs.getBoolean("login_enabled"),
                nullableInstant(rs, "activated_at"));
    }

    private static StaffSessionRow staffSessionRow(ResultSet rs)
            throws SQLException {
        return new StaffSessionRow(
                UUID.fromString(rs.getString("session_uid")),
                instant(rs, "issued_at"),
                instant(rs, "expires_at"),
                nullableInstant(rs, "revoked_at"),
                rs.getLong("auth_version_snapshot"),
                rs.getString("client_kind"),
                rs.getLong("staff_id"),
                UUID.fromString(rs.getString("staff_account_uid")),
                rs.getString("account_kind"),
                rs.getString("display_name"),
                rs.getBoolean("staff_enabled"),
                rs.getLong("staff_auth_version"),
                rs.getLong("binding_id"),
                rs.getString("binding_status"),
                rs.getLong("user_id"),
                rs.getString("phone_e164"),
                nullableInstant(rs, "phone_bound_at"),
                rs.getLong("tenant_id"),
                rs.getString("tenant_code"),
                rs.getString("tenant_status"),
                rs.getLong("organization_id"),
                rs.getString("organization_code"),
                rs.getString("organization_name"),
                rs.getString("organization_status"),
                rs.getLong("miniapp_id"),
                rs.getString("appid"),
                rs.getBoolean("login_enabled"),
                nullableInstant(rs, "activated_at"));
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

    private static String displayName(String nickname) {
        return nickname == null || nickname.isBlank()
                ? "微信用户"
                : nickname;
    }

    private static String audienceName(TrustedAudience audience) {
        return audience == TrustedAudience.MINIAPP
                ? "miniapp"
                : "miniapp-staff";
    }

    private static TargetApiException rejected() {
        return new TargetApiException(
                401,
                "AUTH.SESSION_INVALID",
                "小程序登录状态无效，请重新登录");
    }

    private static void require(boolean condition) {
        if (!condition) {
            throw rejected();
        }
    }

    private record OrdinarySessionRow(
            UUID sessionUid,
            Instant issuedAt,
            Instant expiresAt,
            Instant revokedAt,
            long authVersionSnapshot,
            long userId,
            UUID userUid,
            String phoneE164,
            Instant phoneBoundAt,
            String nickname,
            String userStatus,
            long userAuthVersion,
            long tenantId,
            String tenantCode,
            String tenantStatus,
            long organizationId,
            String organizationCode,
            String organizationName,
            String organizationStatus,
            long miniappId,
            String appId,
            boolean loginEnabled,
            Instant activatedAt) {
    }

    private record StaffSessionRow(
            UUID sessionUid,
            Instant issuedAt,
            Instant expiresAt,
            Instant revokedAt,
            long authVersionSnapshot,
            String clientKind,
            long staffId,
            UUID staffUid,
            String accountKind,
            String displayName,
            boolean staffEnabled,
            long staffAuthVersion,
            long bindingId,
            String bindingStatus,
            long userId,
            String phoneE164,
            Instant phoneBoundAt,
            long tenantId,
            String tenantCode,
            String tenantStatus,
            long organizationId,
            String organizationCode,
            String organizationName,
            String organizationStatus,
            long miniappId,
            String appId,
            boolean loginEnabled,
            Instant activatedAt) {
    }
}
