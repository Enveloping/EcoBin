package org.enveloping.ecobin.identity.application.startdelivery;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Optional;
import java.util.UUID;

@Repository
class JdbcStartDeliveryIdentityLockRepository
        implements StartDeliveryIdentityLockRepository {

    static final String LOCK_TENANT_SQL = """
            SELECT id, tenant_code, status
            FROM iam_tenant
            WHERE id = ?
            FOR UPDATE
            """;

    static final String LOCK_ORGANIZATION_SQL = """
            SELECT id, tenant_id, organization_code, status
            FROM iam_organization
            WHERE id = ?
            FOR UPDATE
            """;

    static final String LOCK_MINIAPP_SQL = """
            SELECT id, tenant_id, organization_id, appid,
                   login_enabled, activated_at
            FROM iam_organization_miniapp
            WHERE id = ?
            FOR UPDATE
            """;

    static final String LOCK_ORGANIZATION_USER_SQL = """
            SELECT id, organization_user_uid, tenant_id,
                   organization_id, organization_miniapp_id,
                   phone_e164, phone_bound_at, status, auth_version
            FROM iam_organization_user
            WHERE id = ?
            FOR UPDATE
            """;

    static final String LOCK_ORGANIZATION_USER_SESSION_SQL = """
            SELECT session_uid, tenant_id, organization_id,
                   organization_miniapp_id, organization_user_id,
                   issued_at, expires_at, revoked_at,
                   auth_version_snapshot
            FROM iam_organization_user_session
            WHERE session_uid = ?
            FOR UPDATE
            """;

    private final JdbcTemplate jdbc;

    JdbcStartDeliveryIdentityLockRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<TenantRow> lockTenant(long tenantId) {
        return jdbc.query(
                        LOCK_TENANT_SQL,
                        (rs, ignored) -> new TenantRow(
                                rs.getLong("id"),
                                rs.getString("tenant_code"),
                                rs.getString("status")),
                        tenantId)
                .stream()
                .findFirst();
    }

    @Override
    public Optional<OrganizationRow> lockOrganization(
            long organizationId) {
        return jdbc.query(
                        LOCK_ORGANIZATION_SQL,
                        (rs, ignored) -> new OrganizationRow(
                                rs.getLong("id"),
                                rs.getLong("tenant_id"),
                                rs.getString("organization_code"),
                                rs.getString("status")),
                        organizationId)
                .stream()
                .findFirst();
    }

    @Override
    public Optional<MiniappRow> lockMiniapp(long miniappId) {
        return jdbc.query(
                        LOCK_MINIAPP_SQL,
                        (rs, ignored) -> new MiniappRow(
                                rs.getLong("id"),
                                rs.getLong("tenant_id"),
                                rs.getLong("organization_id"),
                                rs.getString("appid"),
                                rs.getBoolean("login_enabled"),
                                nullableInstant(rs, "activated_at")),
                        miniappId)
                .stream()
                .findFirst();
    }

    @Override
    public Optional<OrganizationUserRow> lockOrganizationUser(
            long organizationUserId) {
        return jdbc.query(
                        LOCK_ORGANIZATION_USER_SQL,
                        (rs, ignored) -> new OrganizationUserRow(
                                rs.getLong("id"),
                                UUID.fromString(rs.getString(
                                        "organization_user_uid")),
                                rs.getLong("tenant_id"),
                                rs.getLong("organization_id"),
                                rs.getLong("organization_miniapp_id"),
                                rs.getString("phone_e164"),
                                nullableInstant(rs, "phone_bound_at"),
                                rs.getString("status"),
                                rs.getLong("auth_version")),
                        organizationUserId)
                .stream()
                .findFirst();
    }

    @Override
    public Optional<OrganizationUserSessionRow>
            lockOrganizationUserSession(UUID sessionUid) {
        return jdbc.query(
                        LOCK_ORGANIZATION_USER_SESSION_SQL,
                        (rs, ignored) ->
                                new OrganizationUserSessionRow(
                                        UUID.fromString(rs.getString(
                                                "session_uid")),
                                        rs.getLong("tenant_id"),
                                        rs.getLong("organization_id"),
                                        rs.getLong(
                                                "organization_miniapp_id"),
                                        rs.getLong(
                                                "organization_user_id"),
                                        instant(rs, "issued_at"),
                                        instant(rs, "expires_at"),
                                        nullableInstant(rs, "revoked_at"),
                                        rs.getLong(
                                                "auth_version_snapshot")),
                        sessionUid.toString())
                .stream()
                .findFirst();
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
}
