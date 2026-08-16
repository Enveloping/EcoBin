package org.enveloping.ecobin.identity.application.platformminiapp;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
class JdbcPlatformMiniappRepository implements PlatformMiniappRepository {

    private final JdbcTemplate jdbc;

    JdbcPlatformMiniappRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<FactoryOperatorRow> findFactoryOperator(
            long factoryOperatorId,
            UUID factoryOperatorUid,
            boolean forUpdate) {
        return factoryOperator("id = ? AND factory_operator_uid = ?",
                forUpdate,
                factoryOperatorId,
                factoryOperatorUid.toString());
    }

    @Override
    public Optional<FactoryOperatorRow> findFactoryOperatorById(
            long factoryOperatorId,
            boolean forUpdate) {
        return factoryOperator("id = ?", forUpdate, factoryOperatorId);
    }

    @Override
    public Optional<FactoryOperatorRow> findFactoryOperatorByUid(
            UUID factoryOperatorUid,
            boolean forUpdate) {
        return factoryOperator(
                "factory_operator_uid = ?",
                forUpdate,
                factoryOperatorUid.toString());
    }

    private Optional<FactoryOperatorRow> factoryOperator(
            String predicate,
            boolean forUpdate,
            Object... parameters) {
        return jdbc.query("""
                        SELECT id, factory_operator_uid, operator_code,
                               display_name, enabled, auth_version,
                               lock_version
                        FROM iam_factory_operator
                        WHERE %s
                        %s
                        """.formatted(predicate, lockClause(forUpdate)),
                (rs, ignored) -> factoryOperatorRow(rs),
                parameters).stream().findFirst();
    }

    @Override
    public Optional<MiniappChannelRow> findChannelByAppId(
            String appId,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, appid, app_secret, login_enabled,
                               activated_at
                        FROM iam_miniapp_channel
                        WHERE appid = ?
                        """ + lockClause(forUpdate),
                (rs, ignored) -> channelRow(rs),
                appId).stream().findFirst();
    }

    @Override
    public List<MiniappChannelRow> findEnabledChannels() {
        return jdbc.query("""
                        SELECT id, appid, app_secret, login_enabled,
                               activated_at
                        FROM iam_miniapp_channel
                        WHERE login_enabled = 1
                          AND activated_at IS NOT NULL
                          AND app_secret IS NOT NULL
                        ORDER BY id
                        """,
                (rs, ignored) -> channelRow(rs));
    }

    @Override
    public Optional<WechatSubjectRow> findWechatSubject(
            long channelId,
            String openid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, wechat_subject_uid, status
                        FROM iam_wechat_subject
                        WHERE miniapp_channel_id = ? AND openid = ?
                        """ + lockClause(forUpdate),
                (rs, ignored) -> new WechatSubjectRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString(
                                "wechat_subject_uid")),
                        rs.getString("status")),
                channelId,
                openid).stream().findFirst();
    }

    @Override
    public void ensureWechatSubject(
            UUID subjectUid,
            long channelId,
            String openid,
            Instant now) {
        jdbc.update("""
                        INSERT INTO iam_wechat_subject (
                            wechat_subject_uid, miniapp_channel_id,
                            openid, status, auth_version, lock_version,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, 'ACTIVE', 0, 0, ?, ?)
                        ON DUPLICATE KEY UPDATE
                            lock_version = lock_version
                        """,
                subjectUid.toString(),
                channelId,
                openid,
                timestamp(now),
                timestamp(now));
    }

    @Override
    public Optional<BindingIntentRow> findBindingIntent(
            byte[] bindingTokenSha256,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, binding_intent_uid,
                               factory_operator_id, miniapp_channel_id,
                               status, expires_at, consumed_at,
                               consumed_wechat_subject_id
                        FROM iam_factory_operator_binding_intent
                        WHERE binding_token_sha256 = ?
                        """ + lockClause(forUpdate),
                (rs, ignored) -> new BindingIntentRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString(
                                "binding_intent_uid")),
                        rs.getLong("factory_operator_id"),
                        rs.getLong("miniapp_channel_id"),
                        rs.getString("status"),
                        instant(rs, "expires_at"),
                        nullableInstant(rs, "consumed_at"),
                        nullableLong(rs, "consumed_wechat_subject_id")),
                bindingTokenSha256).stream().findFirst();
    }

    @Override
    public int expirePendingBindingIntents(
            long factoryOperatorId,
            Instant now) {
        return jdbc.update("""
                        UPDATE iam_factory_operator_binding_intent
                        SET status = 'EXPIRED'
                        WHERE factory_operator_id = ?
                          AND status = 'PENDING'
                          AND expires_at <= ?
                        """,
                factoryOperatorId,
                timestamp(now));
    }

    @Override
    public int cancelPendingBindingIntents(long factoryOperatorId) {
        return jdbc.update("""
                        UPDATE iam_factory_operator_binding_intent
                        SET status = 'CANCELLED'
                        WHERE factory_operator_id = ?
                          AND status = 'PENDING'
                        """,
                factoryOperatorId);
    }

    @Override
    public void insertBindingIntent(
            UUID bindingIntentUid,
            long factoryOperatorId,
            long channelId,
            long createdByPlatformAdminId,
            byte[] bindingTokenSha256,
            Instant expiresAt,
            Instant createdAt) {
        jdbc.update("""
                        INSERT INTO iam_factory_operator_binding_intent (
                            binding_intent_uid, factory_operator_id,
                            miniapp_channel_id,
                            created_by_platform_admin_id,
                            binding_token_sha256, status, expires_at,
                            consumed_at, consumed_wechat_subject_id,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?, NULL, NULL, ?)
                        """,
                bindingIntentUid.toString(),
                factoryOperatorId,
                channelId,
                createdByPlatformAdminId,
                bindingTokenSha256,
                timestamp(expiresAt),
                timestamp(createdAt));
    }

    @Override
    public int consumeBindingIntent(
            long intentId,
            long wechatSubjectId,
            Instant consumedAt) {
        return jdbc.update("""
                        UPDATE iam_factory_operator_binding_intent
                        SET status = 'CONSUMED', consumed_at = ?,
                            consumed_wechat_subject_id = ?
                        WHERE id = ?
                          AND status = 'PENDING'
                          AND expires_at > ?
                        """,
                timestamp(consumedAt),
                wechatSubjectId,
                intentId,
                timestamp(consumedAt));
    }

    @Override
    public Optional<FactoryMiniappBindingRow> findActiveBindingByOperator(
            long factoryOperatorId,
            boolean forUpdate) {
        return binding("factory_operator_id = ? AND status = 'ACTIVE'",
                forUpdate,
                factoryOperatorId);
    }

    @Override
    public Optional<FactoryMiniappBindingRow> findActiveBindingBySubject(
            long channelId,
            long wechatSubjectId,
            boolean forUpdate) {
        return binding("""
                        miniapp_channel_id = ?
                          AND wechat_subject_id = ?
                          AND status = 'ACTIVE'
                        """,
                forUpdate,
                channelId,
                wechatSubjectId);
    }

    private Optional<FactoryMiniappBindingRow> binding(
            String predicate,
            boolean forUpdate,
            Object... parameters) {
        return jdbc.query("""
                        SELECT id, binding_uid, factory_operator_id,
                               miniapp_channel_id, wechat_subject_id,
                               status, bound_at, revoked_at
                        FROM iam_factory_operator_miniapp_binding
                        WHERE %s
                        %s
                        """.formatted(predicate, lockClause(forUpdate)),
                (rs, ignored) -> new FactoryMiniappBindingRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("binding_uid")),
                        rs.getLong("factory_operator_id"),
                        rs.getLong("miniapp_channel_id"),
                        rs.getLong("wechat_subject_id"),
                        rs.getString("status"),
                        instant(rs, "bound_at"),
                        nullableInstant(rs, "revoked_at")),
                parameters).stream().findFirst();
    }

    @Override
    public void insertBinding(
            UUID bindingUid,
            long factoryOperatorId,
            long channelId,
            long wechatSubjectId,
            Instant now) {
        jdbc.update("""
                        INSERT INTO iam_factory_operator_miniapp_binding (
                            binding_uid, factory_operator_id,
                            miniapp_channel_id, wechat_subject_id,
                            status, bound_at, revoked_at,
                            revocation_reason, lock_version,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, NULL, NULL, 0, ?, ?)
                        """,
                bindingUid.toString(),
                factoryOperatorId,
                channelId,
                wechatSubjectId,
                timestamp(now),
                timestamp(now),
                timestamp(now));
    }

    @Override
    public void insertSession(
            UUID sessionUid,
            long factoryOperatorId,
            long bindingId,
            long channelId,
            long wechatSubjectId,
            Instant issuedAt,
            Instant expiresAt,
            long authVersionSnapshot) {
        jdbc.update("""
                        INSERT INTO iam_factory_operator_miniapp_session (
                            session_uid, factory_operator_id,
                            factory_operator_miniapp_binding_id,
                            miniapp_channel_id, wechat_subject_id,
                            issued_at, expires_at, revoked_at,
                            revocation_reason, auth_version_snapshot,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                        """,
                sessionUid.toString(),
                factoryOperatorId,
                bindingId,
                channelId,
                wechatSubjectId,
                timestamp(issuedAt),
                timestamp(expiresAt),
                authVersionSnapshot,
                timestamp(issuedAt));
    }

    @Override
    public Optional<FactoryMiniappSessionRow> findSession(UUID sessionUid) {
        return jdbc.query("""
                        SELECT s.session_uid, s.issued_at, s.expires_at,
                               s.revoked_at, s.auth_version_snapshot,
                               o.id AS factory_operator_id,
                               o.factory_operator_uid, o.operator_code,
                               o.display_name,
                               o.enabled AS factory_operator_enabled,
                               o.auth_version AS factory_operator_auth_version,
                               b.status AS binding_status,
                               b.revoked_at AS binding_revoked_at,
                               m.login_enabled AS channel_login_enabled,
                               m.activated_at AS channel_activated_at,
                               w.status AS subject_status
                        FROM iam_factory_operator_miniapp_session s
                        JOIN iam_factory_operator o
                          ON o.id = s.factory_operator_id
                        JOIN iam_factory_operator_miniapp_binding b
                          ON b.factory_operator_id = s.factory_operator_id
                         AND b.miniapp_channel_id = s.miniapp_channel_id
                         AND b.wechat_subject_id = s.wechat_subject_id
                         AND b.id = s.factory_operator_miniapp_binding_id
                        JOIN iam_miniapp_channel m
                          ON m.id = s.miniapp_channel_id
                        JOIN iam_wechat_subject w
                          ON w.miniapp_channel_id = s.miniapp_channel_id
                         AND w.id = s.wechat_subject_id
                        WHERE s.session_uid = ?
                        """,
                (rs, ignored) -> new FactoryMiniappSessionRow(
                        UUID.fromString(rs.getString("session_uid")),
                        instant(rs, "issued_at"),
                        instant(rs, "expires_at"),
                        nullableInstant(rs, "revoked_at"),
                        rs.getLong("auth_version_snapshot"),
                        rs.getLong("factory_operator_id"),
                        UUID.fromString(rs.getString(
                                "factory_operator_uid")),
                        rs.getString("operator_code"),
                        rs.getString("display_name"),
                        rs.getBoolean("factory_operator_enabled"),
                        rs.getLong("factory_operator_auth_version"),
                        rs.getString("binding_status"),
                        nullableInstant(rs, "binding_revoked_at"),
                        rs.getBoolean("channel_login_enabled"),
                        nullableInstant(rs, "channel_activated_at"),
                        rs.getString("subject_status")),
                sessionUid.toString()).stream().findFirst();
    }

    @Override
    public int revokeSession(
            UUID sessionUid,
            long factoryOperatorId,
            Instant revokedAt,
            String reason) {
        return jdbc.update("""
                        UPDATE iam_factory_operator_miniapp_session
                        SET revoked_at = ?, revocation_reason = ?
                        WHERE session_uid = ?
                          AND factory_operator_id = ?
                          AND revoked_at IS NULL
                        """,
                timestamp(revokedAt),
                reason,
                sessionUid.toString(),
                factoryOperatorId);
    }

    @Override
    public int revokeActiveSessions(
            long factoryOperatorId,
            Instant revokedAt,
            String reason) {
        return jdbc.update("""
                        UPDATE iam_factory_operator_miniapp_session
                        SET revoked_at = ?, revocation_reason = ?
                        WHERE factory_operator_id = ?
                          AND revoked_at IS NULL
                        """,
                timestamp(revokedAt),
                reason,
                factoryOperatorId);
    }

    @Override
    public int revokeActiveBinding(
            long factoryOperatorId,
            Instant revokedAt,
            String reason) {
        return jdbc.update("""
                        UPDATE iam_factory_operator_miniapp_binding
                        SET status = 'REVOKED', revoked_at = ?,
                            revocation_reason = ?, lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE factory_operator_id = ?
                          AND status = 'ACTIVE'
                        """,
                timestamp(revokedAt),
                reason,
                timestamp(revokedAt),
                factoryOperatorId);
    }

    private static FactoryOperatorRow factoryOperatorRow(ResultSet rs)
            throws SQLException {
        return new FactoryOperatorRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("factory_operator_uid")),
                rs.getString("operator_code"),
                rs.getString("display_name"),
                rs.getBoolean("enabled"),
                rs.getLong("auth_version"),
                rs.getLong("lock_version"));
    }

    private static MiniappChannelRow channelRow(ResultSet rs)
            throws SQLException {
        return new MiniappChannelRow(
                rs.getLong("id"),
                rs.getString("appid"),
                rs.getString("app_secret"),
                rs.getBoolean("login_enabled"),
                nullableInstant(rs, "activated_at"));
    }

    private static String lockClause(boolean forUpdate) {
        return forUpdate ? "FOR UPDATE" : "";
    }

    private static Timestamp timestamp(Instant instant) {
        return Timestamp.valueOf(LocalDateTime.ofInstant(
                instant, ZoneOffset.UTC));
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

    private static Long nullableLong(ResultSet rs, String column)
            throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }
}
