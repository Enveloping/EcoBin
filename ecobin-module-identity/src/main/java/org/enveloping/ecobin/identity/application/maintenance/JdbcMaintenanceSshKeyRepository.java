package org.enveloping.ecobin.identity.application.maintenance;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
class JdbcMaintenanceSshKeyRepository
        implements MaintenanceSshKeyRepository {

    private static final String KEY_COLUMNS = """
            k.id,
            k.maintenance_ssh_key_uid,
            k.platform_admin_id,
            k.label,
            k.public_key,
            k.fingerprint_sha256,
            k.revoked_at,
            k.revoked_reason,
            k.lock_version,
            k.created_at,
            k.updated_at
            """;

    private final JdbcTemplate jdbc;

    JdbcMaintenanceSshKeyRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<PlatformAdminRow> findPlatformAdmin(
            long platformAdminId,
            UUID platformAdminUid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, platform_admin_uid, enabled,
                               auth_version, deleted_at
                        FROM iam_platform_admin
                        WHERE id = ?
                          AND platform_admin_uid = ?
                        """ + lockClause(forUpdate),
                (rs, ignored) -> new PlatformAdminRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString(
                                "platform_admin_uid")),
                        rs.getBoolean("enabled"),
                        rs.getLong("auth_version"),
                        nullableInstant(rs, "deleted_at")),
                platformAdminId,
                platformAdminUid.toString()).stream().findFirst();
    }

    @Override
    public List<MaintenanceSshKeyRow> findAll(long platformAdminId) {
        return jdbc.query("""
                        SELECT %s
                        FROM iam_platform_admin_maintenance_ssh_key k
                        WHERE k.platform_admin_id = ?
                        ORDER BY CASE WHEN k.revoked_at IS NULL THEN 0 ELSE 1 END,
                                 k.created_at DESC,
                                 k.id DESC
                        """.formatted(KEY_COLUMNS),
                (rs, ignored) -> keyRow(rs),
                platformAdminId);
    }

    @Override
    public Optional<MaintenanceSshKeyRow> findByUid(
            long platformAdminId,
            UUID maintenanceSshKeyUid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT %s
                        FROM iam_platform_admin_maintenance_ssh_key k
                        WHERE k.platform_admin_id = ?
                          AND k.maintenance_ssh_key_uid = ?
                        %s
                        """.formatted(
                        KEY_COLUMNS,
                        lockClause(forUpdate)),
                (rs, ignored) -> keyRow(rs),
                platformAdminId,
                maintenanceSshKeyUid.toString()).stream().findFirst();
    }

    @Override
    public Optional<MaintenanceSshKeyRow> findByFingerprint(
            long platformAdminId,
            String fingerprintSha256) {
        return jdbc.query("""
                        SELECT %s
                        FROM iam_platform_admin_maintenance_ssh_key k
                        WHERE k.platform_admin_id = ?
                          AND k.fingerprint_sha256 = ?
                        """.formatted(KEY_COLUMNS),
                (rs, ignored) -> keyRow(rs),
                platformAdminId,
                fingerprintSha256).stream().findFirst();
    }

    @Override
    public Optional<MaintenanceSshKeyRow> findActive(
            UUID platformAdminUid,
            UUID maintenanceSshKeyUid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT %s
                        FROM iam_platform_admin_maintenance_ssh_key k
                        JOIN iam_platform_admin a
                          ON a.id = k.platform_admin_id
                        WHERE a.platform_admin_uid = ?
                          AND a.enabled = 1
                          AND a.deleted_at IS NULL
                          AND k.maintenance_ssh_key_uid = ?
                          AND k.revoked_at IS NULL
                        %s
                        """.formatted(
                        KEY_COLUMNS,
                        lockClause(forUpdate)),
                (rs, ignored) -> keyRow(rs),
                platformAdminUid.toString(),
                maintenanceSshKeyUid.toString()).stream().findFirst();
    }

    @Override
    public void insert(
            UUID maintenanceSshKeyUid,
            long platformAdminId,
            String label,
            String publicKey,
            String fingerprintSha256) {
        jdbc.update("""
                        INSERT INTO iam_platform_admin_maintenance_ssh_key (
                            maintenance_ssh_key_uid,
                            platform_admin_id,
                            label,
                            public_key,
                            fingerprint_sha256,
                            revoked_at,
                            revoked_reason,
                            lock_version,
                            created_at,
                            updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, NULL, NULL, 0,
                            CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
                        )
                        """,
                maintenanceSshKeyUid.toString(),
                platformAdminId,
                label,
                publicKey,
                fingerprintSha256);
    }

    @Override
    public int revoke(
            long keyId,
            long expectedVersion,
            String reason) {
        return jdbc.update("""
                        UPDATE iam_platform_admin_maintenance_ssh_key
                        SET revoked_at = CURRENT_TIMESTAMP(3),
                            revoked_reason = ?,
                            lock_version = lock_version + 1,
                            updated_at = CURRENT_TIMESTAMP(3)
                        WHERE id = ?
                          AND lock_version = ?
                          AND revoked_at IS NULL
                        """,
                reason,
                keyId,
                expectedVersion);
    }

    private static MaintenanceSshKeyRow keyRow(ResultSet rs)
            throws SQLException {
        return new MaintenanceSshKeyRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString(
                        "maintenance_ssh_key_uid")),
                rs.getLong("platform_admin_id"),
                rs.getString("label"),
                rs.getString("public_key"),
                rs.getString("fingerprint_sha256"),
                nullableInstant(rs, "revoked_at"),
                rs.getString("revoked_reason"),
                rs.getLong("lock_version"),
                instant(rs, "created_at"),
                instant(rs, "updated_at"));
    }

    private static String lockClause(boolean forUpdate) {
        return forUpdate ? "FOR UPDATE" : "";
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
